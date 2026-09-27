"""知识点标注与难度评估（同一次大模型调用完成）。

按试卷学科、学段选用知识树：
- 小树（末级知识点 ≤ FULL_LIST_MAX）：把完整清单交给大模型，只能按编号选择；
- 大树：先让大模型写出知识点名称，再按名称检索候选，最后让大模型从候选中确定；
- 无知识树：大模型按教材常用说法生成名称。
知识树中确实没有的知识点保留名称，标记为「不在知识树中」，便于补充知识树。
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import DraftQuestion, KnowledgeNode, KnowledgeTree, ParseJob, SessionLocal
from ..knowledge_tree import leaves, pick_tree, search_nodes
from .llm import LLMError, chat_json, describe, items_of

log = logging.getLogger(__name__)

BATCH = 15
MAX_KPS = 3
FULL_LIST_MAX = 300
CANDIDATES_PER_NAME = 6
# 大树模式下候选选择失败时，名称匹配度达到该值直接采用
DIRECT_MATCH = 0.85

DIFFICULTY_RUBRIC = """difficulty 为难度系数 0–1，越高越难：0.1–0.3 直接运用概念或公式的基础题；0.3–0.6 需要两三步推理或常规方法；
0.6–0.8 综合多个知识点或需要较强分析；0.8–1 压轴题或需要创造性思路。结合题型、分值和题目在试卷中的位置判断，保留两位小数。"""

COMMON = """你是经验丰富的中国中小学{subject}教研员，熟悉{textbook}教材。下面是一份{stage}{grade}试卷中的题目。"""

FREE_SYSTEM = COMMON + """请为每道题标注考查的知识点并评估难度，只输出 JSON：
{{"questions": [{{"no": 1, "kps": ["集合的基本运算", "一元二次不等式"], "difficulty": 0.35}}]}}
要求：
1. 每题 1–{max_kps} 个知识点，按考查主次排序；使用教材章节中的规范名称，简洁（一般不超过 12 个字），不要写成句子、不要带编号。
2. 同一个知识点在不同题中写法保持一致；每道输入的题都要输出，no 与输入一致。
3. """ + DIFFICULTY_RUBRIC

TREE_SYSTEM = COMMON + """本校使用的知识点清单如下（编号：路径）：
{catalog}

请为每道题从清单中选出考查的 1–{max_kps} 个知识点编号（按考查主次排序），并评估难度，只输出 JSON：
{{"questions": [{{"no": 1, "ids": ["k12", "k30"], "other": [], "difficulty": 0.35}}]}}
要求：
1. ids 只能使用清单中的编号；清单中确实没有对应知识点时，可在 other 中写出知识点名称（最多 1 个）。
2. 每道输入的题都要输出，no 与输入一致。
3. """ + DIFFICULTY_RUBRIC

CHOOSE_SYSTEM = """你是{subject}教研员。每道题下面列出了知识树中的候选知识点（编号：路径），请为每道题选出 1–{max_kps} 个最符合的编号，
按考查主次排序；候选中都不符合时 ids 留空。只输出 JSON：{{"questions": [{{"no": 1, "ids": ["c3", "c7"]}}]}}"""


@dataclass
class TagResult:
    tagged: int = 0
    kinds: int = 0
    tree_name: str | None = None
    unmatched: int = 0  # 不在知识树中的知识点数
    difficulty: dict[str, float] = field(default_factory=dict)


def kp_id(subject: str, name: str) -> str:
    """不在知识树中的知识点：同一学科下同名知识点得到相同 id。"""
    return "kp_" + hashlib.md5(f"{subject}:{name}".encode()).hexdigest()[:10]


def _clean(name: Any) -> str:
    name = re.sub(r"^\s*[\d一二三四五六七八九十]+[.、．)）]\s*", "", str(name)).strip(" ，,；;。")
    return name[:30]


def _difficulty(v: Any) -> float | None:
    try:
        d = float(v)
    except (TypeError, ValueError):
        return None
    return round(d, 2) if 0 <= d <= 1 else None


def _payload(qs: list[DraftQuestion]) -> str:
    rows = []
    for q in qs:
        opts = "；".join(f"{'ABCDEFGH'[i]}．{o}" for i, o in enumerate(q.options))
        head = f"第 {q.no} 题（{q.type}，{q.score:g} 分{'，含配图' if q.images else ''}）"
        rows.append(f"{head}：{q.stem[:400]}" + (f"\n选项：{opts[:200]}" if opts else ""))
    return "\n\n".join(rows)


def _node_ref(n: KnowledgeNode) -> dict[str, Any]:
    return {"id": n.id, "name": n.name, "path": n.path, "inTree": True}


def _free_ref(subject: str, name: str) -> dict[str, Any]:
    return {"id": kp_id(subject, name), "name": name, "path": None, "inTree": False}


def _items(data: Any) -> list[dict[str, Any]]:
    return [x for x in items_of(data, "questions") or [] if isinstance(x, dict)]


class Tagger:
    def __init__(self, meta: dict[str, Any], tree: KnowledgeTree | None, nodes: list[KnowledgeNode]):
        self.s = get_settings()
        self.meta = meta
        self.subject = meta.get("subject") or ""
        self.tree = tree
        self.nodes = nodes
        self.fmt = dict(subject=self.subject or "学科", textbook=meta.get("textbook") or "现行", stage=meta.get("stage") or "",
                        grade=meta.get("grade") or "", max_kps=MAX_KPS)

    async def run(self, qs: list[DraftQuestion]) -> tuple[dict[str, list[dict]], dict[str, float]]:
        kps: dict[str, list[dict]] = {}
        diff: dict[str, float] = {}
        for i in range(0, len(qs), BATCH):
            chunk = qs[i:i + BATCH]
            try:
                if self.tree and len(self.nodes) <= FULL_LIST_MAX:
                    k, d = await self._full_list(chunk)
                elif self.tree:
                    k, d = await self._retrieve(chunk)
                else:
                    k, d = await self._free(chunk)
            except LLMError as e:
                log.warning("知识点标注失败（第 %d 批）：%s", i // BATCH + 1, describe(e))
                continue
            kps.update(k)
            diff.update(d)
        return kps, diff

    def _names(self, raw: Any) -> list[str]:
        out: list[str] = []
        for n in raw or []:
            n = _clean(n)
            if n and n not in out:
                out.append(n)
        return out

    async def _free(self, chunk: list[DraftQuestion]):
        data = await chat_json(FREE_SYSTEM.format(**self.fmt), _payload(chunk), self.s, purpose="knowledge")
        by_no = {q.no: q for q in chunk}
        kps, diff = {}, {}
        for it in _items(data):
            q = by_no.get(it.get("no"))
            if not q:
                continue
            names = self._names(it.get("kps"))[:MAX_KPS]
            if names:
                kps[q.id] = [_free_ref(self.subject, n) for n in names]
            if (d := _difficulty(it.get("difficulty"))) is not None:
                diff[q.id] = d
        return kps, diff

    async def _full_list(self, chunk: list[DraftQuestion]):
        catalog = "\n".join(f"k{i}：{n.path}" for i, n in enumerate(self.nodes))
        data = await chat_json(TREE_SYSTEM.format(catalog=catalog, **self.fmt), _payload(chunk), self.s, purpose="knowledge")
        by_no = {q.no: q for q in chunk}
        kps, diff = {}, {}
        for it in _items(data):
            q = by_no.get(it.get("no"))
            if not q:
                continue
            refs: list[dict] = []
            for raw in it.get("ids") or []:
                m = re.fullmatch(r"k(\d+)", str(raw).strip())
                if m and int(m.group(1)) < len(self.nodes):
                    ref = _node_ref(self.nodes[int(m.group(1))])
                    if all(r["id"] != ref["id"] for r in refs):
                        refs.append(ref)
            for name in self._names(it.get("other"))[:1]:
                refs.append(self._resolve_name(name))
            if refs:
                kps[q.id] = refs[:MAX_KPS]
            if (d := _difficulty(it.get("difficulty"))) is not None:
                diff[q.id] = d
        return kps, diff

    def _resolve_name(self, name: str) -> dict[str, Any]:
        """模型给出的名称若与知识树节点高度匹配则归入该节点，否则作为树外知识点。"""
        best = search_nodes(self.nodes, name, limit=1, min_score=DIRECT_MATCH)
        return _node_ref(best[0].node) if best else _free_ref(self.subject, name)

    async def _retrieve(self, chunk: list[DraftQuestion]):
        """大树：名称 → 候选 → 选择。"""
        free, diff = await self._free(chunk)
        cands: dict[str, list[KnowledgeNode]] = {}
        for q in chunk:
            seen: dict[str, KnowledgeNode] = {}
            for ref in free.get(q.id, []):
                for m in search_nodes(self.nodes, ref["name"], limit=CANDIDATES_PER_NAME):
                    seen.setdefault(m.node.id, m.node)
            cands[q.id] = list(seen.values())[:CANDIDATES_PER_NAME * MAX_KPS]
        lines, index = [], {}
        for q in chunk:
            if not cands[q.id]:
                continue
            lines.append(f"第 {q.no} 题：{q.stem[:200]}")
            for n in cands[q.id]:
                cid = f"c{len(index)}"
                index[cid] = n
                lines.append(f"  {cid}：{n.path}")
        chosen: dict[str, list[dict]] = {}
        if index:
            try:
                data = await chat_json(CHOOSE_SYSTEM.format(**self.fmt), "\n".join(lines), self.s, purpose="knowledge")
                by_no = {q.no: q for q in chunk}
                for it in _items(data):
                    q = by_no.get(it.get("no"))
                    if not q:
                        continue
                    allowed = {n.id for n in cands[q.id]}
                    refs = [_node_ref(index[c]) for c in it.get("ids") or [] if c in index and index[c].id in allowed]
                    if refs:
                        chosen[q.id] = list({r["id"]: r for r in refs}.values())[:MAX_KPS]
            except LLMError as e:
                log.warning("知识点候选选择失败，按名称匹配：%s", describe(e))
        kps = {}
        for q in chunk:
            if q.id in chosen:
                kps[q.id] = chosen[q.id]
            elif q.id in free:
                kps[q.id] = list({r["id"]: r for r in (self._resolve_name(f["name"]) for f in free[q.id])}.values())
        return kps, diff


async def tag_job(job_id: str, *, only_missing: bool = False) -> TagResult:
    """为本卷题目标注知识点并评估难度（难度由调用方决定是否采用）。失败的批次跳过。"""
    with SessionLocal() as s:
        job = s.get(ParseJob, job_id)
        meta = dict((job.meta if job else None) or {})
        qs = list(s.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job_id).order_by(DraftQuestion.no)))
        tree = pick_tree(s, job.school_id, meta) if job else None
        nodes = leaves(s, tree.id) if tree else []
        tree_name = tree.name if tree else None
    if only_missing:
        qs = [q for q in qs if not q.knowledge_points]
    kps, diff = await Tagger(meta, tree, nodes).run(qs)
    with SessionLocal() as s:
        for qid, refs in kps.items():
            q = s.get(DraftQuestion, qid)
            if q is not None:
                q.knowledge_points = refs
        s.commit()
    flat = [r for refs in kps.values() for r in refs]
    return TagResult(tagged=len(kps), kinds=len({r["id"] for r in flat}), tree_name=tree_name,
                     unmatched=len({r["id"] for r in flat if not r.get("inTree")}), difficulty=diff)
