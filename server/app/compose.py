"""AI 组卷（Demo）。

大模型只负责把老师的描述（学生情况、要求、多轮修改）整理成组卷蓝图：各题型题量、重点知识点、目标难度与总分；
选题与赋分由程序完成，保证题目都来自题库、总分准确。未配置大模型或调用失败时，按关键词匹配知识点、按默认结构组卷。

- 知识点清单取自题库中已有题目的知识点（含各级上级），大模型只能按编号选择，选中的知识点一定有题；
- 选题：逐题贪心，兼顾重点知识点权重、知识点分散（同一知识点已选的题越多，再选的收益越低）与平均难度接近目标；
- 赋分：按题型基础分值等比缩放，选择、填空同一大题分值相同，解答题按难度分配余下的分数。
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bank import bank_out
from .config import get_settings
from .db import BankQuestion
from .knowledge_tree import PATH_SEP
from .pipeline.llm import LLMError, chat_json, describe
from .schemas import (
    QUESTION_TYPES, ComposeFocus, ComposeItem, ComposeMessage, ComposeRequest, ComposeResult, ComposeSection,
)
from .similar import DUPLICATE_SCORE, lexical

log = logging.getLogger(__name__)

# 每题基础分值（总分 150 左右的常见结构），按总分等比缩放
BASE_SCORE = {"单选题": 5, "多选题": 6, "填空题": 5, "解答题": 12}
DEFAULT_COUNTS = {"单选题": 8, "多选题": 3, "填空题": 3, "解答题": 5}
MAX_QUESTIONS = 40
MAX_PER_TYPE = 20
CATALOG_MAX = 200
# 平均难度偏离目标的扣分系数（重点知识点收益一般在 0–3 之间）
DIFF_PENALTY = 4.0
DIFF_WARN = 0.1

SYSTEM = """你是经验丰富的中国{stage}{subject}教研员，正在帮老师为学生组一份练习卷。
题库中可用的知识点如下（编号：知识点路径（题数））：
{catalog}

各题型可用题数：{types}

根据老师描述的学生情况（年级、薄弱点、考试目标等）和对话中的要求，给出组卷蓝图，只输出 JSON：
{{"reply": "组卷思路", "title": "试卷标题", "total": 100, "difficulty": 0.45,
  "sections": [{{"type": "单选题", "count": 8}}], "focus": [{{"id": "k3", "weight": 3}}], "avoid": ["k7"]}}
要求：
1. reply：用一两句话向老师说明这次的组卷思路和调整，面向老师，不要出现编号。
2. total 为总分，difficulty 为平均难度（0–1，越高越难：0.3 以下容易，0.3–0.6 适中，0.6 以上较难）。
   默认沿用老师的当前设置；对话中要求改变时（如「再简单一点」「满分 120」）相应调整。
3. sections 为各题型题量：题型只能从可用题型中选，题量不超过可用题数，合计不超过 {max_questions} 道。
4. focus 为重点考查的知识点编号及权重 1–3（学生薄弱点、老师点名的知识点权重高）；
   老师没有提到具体知识点时 focus 留空，表示覆盖全部知识点。
5. avoid 为老师明确要求不考的知识点编号，没有则留空。
6. 以老师最后一条消息为准，同时保留此前对话中仍然有效的要求。"""


@dataclass
class Topic:
    """知识点清单中的一项：题目知识点或其上级。key 为路径（不在知识树中的为名称）。"""

    code: str
    key: str
    name: str
    count: int


@dataclass
class Blueprint:
    title: str
    total: int
    difficulty: float
    counts: dict[str, int]
    focus: dict[str, int] = field(default_factory=dict)  # topic key → 权重
    avoid: set[str] = field(default_factory=set)
    reply: str = ""
    ai: bool = False


def _kp_key(k: dict[str, Any]) -> str:
    return (k.get("path") or k.get("name") or "").strip()


def build_catalog(questions: list[BankQuestion], limit: int | None = CATALOG_MAX) -> list[Topic]:
    """题库中出现的知识点及其各级上级（不含最顶层的模块），按题数从多到少，最多 limit 个。"""
    counts: Counter[str] = Counter()
    for q in questions:
        keys: set[str] = set()
        for k in q.knowledge_points or []:
            key = _kp_key(k)
            if not key:
                continue
            parts = key.split(PATH_SEP)
            keys.update(PATH_SEP.join(parts[:i]) for i in range(min(2, len(parts)), len(parts) + 1))
        counts.update(keys)
    top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]
    return [Topic(code=f"k{i}", key=key, name=key.split(PATH_SEP)[-1], count=n) for i, (key, n) in enumerate(top, 1)]


def _matches(q: BankQuestion, key: str) -> bool:
    return any((k2 := _kp_key(k)) == key or k2.startswith(key + PATH_SEP) for k in q.knowledge_points or [])


def default_counts(available: Counter[str], total: int) -> dict[str, int]:
    scale = min(1.0, max(0.4, total / 120))
    return {t: min(available[t], max(1, round(n * scale))) for t, n in DEFAULT_COUNTS.items() if available[t]}


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def parse_blueprint(raw: Any, req: ComposeRequest, catalog: list[Topic], available: Counter[str]) -> Blueprint:
    """校验大模型输出的蓝图：非法的题型、编号、数值丢弃或截断到合法范围。"""
    raw = raw if isinstance(raw, dict) else {}
    by_code = {t.code: t for t in catalog}
    total = int(_clamp(raw.get("total"), 10, 300, req.total))
    counts: dict[str, int] = {}
    for sec in raw.get("sections") or []:
        if not isinstance(sec, dict) or sec.get("type") not in QUESTION_TYPES or not available[sec["type"]]:
            continue
        n = int(_clamp(sec.get("count"), 0, min(MAX_PER_TYPE, available[sec["type"]]), 0))
        if n:
            counts[sec["type"]] = n
    counts = counts or default_counts(available, total)
    # 超过总题量上限时从后面的题型减
    over = sum(counts.values()) - MAX_QUESTIONS
    for t in reversed(list(counts)):
        if over <= 0:
            break
        cut = min(over, counts[t] - 1)
        counts[t] -= cut
        over -= cut
    focus: dict[str, int] = {}
    for f in raw.get("focus") or []:
        if isinstance(f, dict) and (t := by_code.get(str(f.get("id")))):
            focus[t.key] = int(_clamp(f.get("weight"), 1, 3, 1))
    avoid = {by_code[str(c)].key for c in raw.get("avoid") or [] if str(c) in by_code}
    return Blueprint(
        title=str(raw.get("title") or "").strip()[:40] or f"{req.stage}{req.subject}练习卷",
        total=total, difficulty=round(_clamp(raw.get("difficulty"), 0.1, 0.9, req.difficulty), 2),
        counts=counts, focus=focus, avoid=avoid - set(focus), reply=str(raw.get("reply") or "").strip()[:300], ai=True,
    )


def keyword_blueprint(req: ComposeRequest, catalog: list[Topic], available: Counter[str]) -> Blueprint:
    """未使用大模型时：老师消息中出现的知识点名称作为重点，其余按设置与默认结构。"""
    text = "".join(m.content for m in req.messages if m.role == "user")
    focus = {t.key: 2 for t in catalog if len(t.name) >= 2 and t.name in text}
    # 只保留最具体的：上级与下级都被提到时，去掉上级
    focus = {k: w for k, w in focus.items() if not any(o.startswith(k + PATH_SEP) for o in focus)}
    return Blueprint(title=f"{req.stage}{req.subject}练习卷", total=req.total, difficulty=round(req.difficulty, 2),
                     counts=default_counts(available, req.total), focus=focus)


# ---------------- 选题 ----------------

@dataclass
class Selection:
    sections: list[tuple[str, list[BankQuestion]]]
    gaps: list[str]


def select_questions(questions: list[BankQuestion], bp: Blueprint) -> Selection:
    pool = [q for q in questions if not any(_matches(q, k) for k in bp.avoid)]
    # 每道题覆盖的「主题」：有重点知识点时为命中的重点知识点，否则为题目自身的知识点
    topics: dict[str, dict[str, int]] = {}
    for q in pool:
        if bp.focus:
            topics[q.id] = {k: w for k, w in bp.focus.items() if _matches(q, k)}
        else:
            topics[q.id] = {_kp_key(k): 1 for k in q.knowledge_points or [] if _kp_key(k)}
    covered: Counter[str] = Counter()
    chosen: list[BankQuestion] = []
    sections: list[tuple[str, list[BankQuestion]]] = []
    gaps: list[str] = []

    def gain(q: BankQuestion) -> float:
        t = topics[q.id]
        if not t:
            return 0.0 if bp.focus else 0.1
        return sum(w / (1 + covered[k]) for k, w in t.items())

    def value(q: BankQuestion) -> tuple[bool, float, str]:
        avg = (sum(c.coef for c in chosen) + q.coef) / (len(chosen) + 1)
        # 有重点知识点时，相关的题用完之前不选无关的题
        return bool(topics[q.id]) or not bp.focus, gain(q) - DIFF_PENALTY * abs(avg - bp.difficulty), q.id

    for qtype in QUESTION_TYPES:
        want = bp.counts.get(qtype, 0)
        if not want:
            continue
        cands = sorted((q for q in pool if q.type == qtype), key=lambda q: q.id)
        picked: list[BankQuestion] = []
        while len(picked) < want and cands:
            best = max(cands, key=value)
            cands.remove(best)
            if any(lexical(best.stem, c.stem) >= DUPLICATE_SCORE for c in chosen):
                continue  # 与已选的题几乎相同
            picked.append(best)
            chosen.append(best)
            covered.update(topics[best.id].keys())
        if len(picked) < want:
            gaps.append(f"{qtype}：题库中可用的题只有 {len(picked)} 道，少于要求的 {want} 道")
        if bp.focus:
            off = sum(1 for q in picked if not topics[q.id])
            if off:
                gaps.append(f"{qtype}：与重点知识点相关的题不足，有 {off} 道从其他知识点补充")
        if picked:
            sections.append((qtype, sorted(picked, key=lambda q: (q.coef, q.id))))  # 由易到难
    missed = [k.split(PATH_SEP)[-1] for k in bp.focus if not covered[k]]
    if missed:
        gaps.append("以下重点知识点没有选到题（题库中相应题目不足）：" + "、".join(missed))
    return Selection(sections=sections, gaps=gaps)


# ---------------- 赋分 ----------------

def apportion(weights: list[float], amount: int) -> list[int]:
    """把 amount 分按权重分给各项，每项至少 1 分，合计恰好为 amount（最大余数法）。"""
    n = len(weights)
    if amount < n:
        raise ValueError(f"总分 {amount} 分不足以给 {n} 道题每题至少 1 分")
    extra, total_w = amount - n, sum(weights) or 1
    raw = [extra * w / total_w for w in weights]
    out = [int(x) for x in raw]
    for i in sorted(range(n), key=lambda i: (out[i] - raw[i], i))[: extra - sum(out)]:
        out[i] += 1
    return [x + 1 for x in out]


def _weight(q: BankQuestion) -> float:
    base = BASE_SCORE.get(q.type, 5)
    return base * (0.8 + 0.5 * q.coef) if q.type == "解答题" else base


def assign_scores(sections: list[tuple[str, list[BankQuestion]]], total: int) -> dict[str, int]:
    """选择、填空等同一大题每题分值相同；余下的分数按难度分给解答题（没有解答题时分给最后一个大题）。"""
    items = [q for _, qs in sections for q in qs]
    if not items:
        return {}
    flexible_type = "解答题" if any(t == "解答题" for t, _ in sections) else sections[-1][0]
    factor = total / sum(_weight(q) for q in items)
    scores: dict[str, int] = {}
    for qtype, qs in sections:
        if qtype != flexible_type:
            each = max(1, round(BASE_SCORE.get(qtype, 5) * factor))
            scores.update((q.id, each) for q in qs)
    flexible = next(qs for t, qs in sections if t == flexible_type)
    rest = total - sum(scores.values())
    if rest < len(flexible):  # 固定分值的大题已占满，全部按权重分配
        return dict(zip((q.id for q in items), apportion([_weight(q) for q in items], total)))
    scores.update(zip((q.id for q in flexible), apportion([_weight(q) for q in flexible], rest)))
    return scores


# ---------------- 入口 ----------------

def _conversation(messages: list[ComposeMessage]) -> str:
    return "\n".join(f"{'老师' if m.role == 'user' else '你'}：{m.content}" for m in messages)


async def compose(s: Session, school_id: str, req: ComposeRequest) -> ComposeResult:
    questions = list(s.scalars(select(BankQuestion).where(
        BankQuestion.school_id == school_id,
        BankQuestion.meta["stage"].as_string() == req.stage,
        BankQuestion.meta["subject"].as_string() == req.subject,
    ).order_by(BankQuestion.id)))
    if not questions:
        raise ValueError(f"题库中还没有{req.stage}{req.subject}的题目，请先在「试卷解析」中上传试卷并入库")
    available = Counter(q.type for q in questions)
    catalog = build_catalog(questions)

    bp: Blueprint | None = None
    note = ""
    settings = get_settings()
    if settings.llm_enabled:
        system = SYSTEM.format(
            stage=req.stage, subject=req.subject, max_questions=MAX_QUESTIONS,
            catalog="\n".join(f"{t.code}：{t.key}（{t.count}）" for t in catalog) or "（题目尚未标注知识点）",
            types="，".join(f"{t} {available[t]} 道" for t in QUESTION_TYPES if available[t]),
        )
        user = f"老师的当前设置：总分 {req.total} 分，平均难度 {req.difficulty:.2f}。\n\n对话：\n{_conversation(req.messages)}"
        try:
            bp = parse_blueprint(await chat_json(system, user, settings, purpose="compose"), req, catalog, available)
        except LLMError as e:
            log.warning("AI 组卷：大模型调用失败，改用关键词匹配：%s", describe(e))
            note = "大模型暂时不可用，"
    else:
        note = "未配置大模型，"
    if bp is None:
        # 关键词匹配不受清单长度限制，题数少的知识点也能匹配到
        bp = keyword_blueprint(req, build_catalog(questions, limit=None), available)
        named = "、".join(k.split(PATH_SEP)[-1] for k in bp.focus)
        bp.reply = note + (f"已按您提到的知识点（{named}）和当前设置组卷。" if named else "已按当前设置和默认结构组卷。")

    sel = select_questions(questions, bp)
    total = bp.total
    n = sum(len(qs) for _, qs in sel.sections)
    if n > total:
        raise ValueError(f"总分 {total} 分不足以给 {n} 道题每题至少 1 分，请提高总分或减少题量")
    scores = assign_scores(sel.sections, total)
    actual = round(sum(q.coef * scores[q.id] for _, qs in sel.sections for q in qs) / total, 2) if scores else 0.0
    gaps = list(sel.gaps)
    if not catalog:
        gaps.insert(0, "题库中的题目尚未标注知识点，本次只按题型和难度选题，无法侧重具体知识点")
    if scores and abs(actual - bp.difficulty) > DIFF_WARN:
        gaps.append(f"题库中符合条件的题难度有限，实际平均难度 {actual:.2f}，与目标 {bp.difficulty:.2f} 相差较大")
    if not scores:
        total = 0

    all_items = [q for _, qs in sel.sections for q in qs]
    focus = [
        ComposeFocus(name=k.split(PATH_SEP)[-1], path=k if PATH_SEP in k else None, weight=w,
                     count=sum(1 for q in all_items if _matches(q, k)))
        for k, w in sorted(bp.focus.items(), key=lambda x: -x[1])
    ]
    return ComposeResult(
        reply=bp.reply or "已按要求组卷。", title=bp.title, total=total, difficulty=bp.difficulty, actual_difficulty=actual,
        sections=[ComposeSection(type=t, score=sum(scores[q.id] for q in qs),
                                 items=[ComposeItem(question=bank_out(q), score=scores[q.id]) for q in qs])
                  for t, qs in sel.sections],
        focus=focus, gaps=gaps, ai=bp.ai,
    )
