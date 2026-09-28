"""拆题：规则切分 + 大模型修正。

1. 规则：识别大题标题（一、单选题 …每小题 5 分）、题号、卷末答案区，得到候选切分。
2. 大模型（可选）：只返回「版面单元 id 的分组」，不重写题干，避免编造内容并保留原图位置。
   校验不通过时退回规则结果；两者不一致的题降低置信度，进入「待核对」。
"""

import json
import logging
import re
from dataclasses import dataclass, field

from ..config import Settings
from .ir import Block
from .llm import LLMError, chat_json, items_of

log = logging.getLogger(__name__)

QTYPES = ("单选题", "多选题", "填空题", "解答题")
DEFAULT_SCORE = {"单选题": 5.0, "多选题": 6.0, "填空题": 5.0, "解答题": 12.0}

_CN_NUM = "一二三四五六七八九十"
SECTION_RE = re.compile(rf"^\s*[{_CN_NUM}]{{1,3}}\s*[、.．]\s*(.{{0,60}})$")
SECTION_TYPES = [
    (re.compile(r"多项选择|多选"), "多选题"),
    (re.compile(r"单项选择|单选|选择题"), "单选题"),
    (re.compile(r"填空"), "填空题"),
    # 理化生的实验题、探究题、推断题、科普阅读题等在题库中统一归为解答题
    (re.compile(r"解答|计算|证明|简答|应用|实验|探究|推断|综合|阅读|流程"), "解答题"),
]
SCORE_EACH_RE = re.compile(r"每(?:小)?题\s*(\d+(?:\.\d+)?)\s*分")
# 题号：行首数字 + 顿号/点，排除 1.5 这类小数
Q_START_RE = re.compile(r"^\s*(\d{1,3})\s*[.．、](?!\d)\s*")
Q_SCORE_RE = re.compile(r"^\s*[（(]\s*(?:本小?题满分)?\s*(\d+(?:\.\d+)?)\s*分\s*[)）]\s*")
ANSWER_SECTION_RE = re.compile(r"^\s*[【\[]?\s*(?:参考答案|试题答案|答案(?:与|及|和)?(?:解析|详解)?)\s*[】\]]?\s*(?:[:：].*)?$")
NOTICE_RE = re.compile(r"^\s*[【\[]?\s*(?:注意事项|考生须知|答题要求)")
INLINE_MARK_RE = re.compile(r"【\s*(答案|解析|详解|分析|点睛|点评)\s*】")
BLANK_RE = re.compile(r"_{3,}|＿{2,}|—{3,}")
SUB_MARK_RE = re.compile(r"(?:（\d+）|\(\d+\)|[⑴-⒇])")
OPTION_LINE_RE = re.compile(r"^[A-H]\s*[.．、]")
IMAGE_MARK = "[图]"
CHOICE_ANSWER_RE = re.compile(r"^\s*([A-H](?:\s*[,，、]?\s*[A-H]){0,7})(?![A-Za-z])\s*[.．。;；,，]?\s*")
COMPACT_ANSWER_RE = re.compile(r"(\d{1,3})\s*[-~—–至]\s*(\d{1,3})\s*[:：.．]?\s*([A-H](?:[\s,，、]*[A-H])+)")
_CJK = re.compile(r"[　-〿一-鿿＀-￯]")


@dataclass
class Unit:
    """参与拆题的最小单元，对应一个 Block。"""

    id: str
    seq: int
    page: int
    type: str
    text: str


@dataclass
class Question:
    printed_no: int | None
    type: str
    score: float
    stem: str
    options: list[str]
    answer: str | None
    analysis: str | None
    unit_ids: list[str]
    confidence: float
    section: int = 0
    flags: list[str] = field(default_factory=list)


@dataclass
class Segmentation:
    questions: list[Question]
    preamble: list[Unit]           # 首题之前的标题、说明，用于试卷分类
    used_llm: bool = False
    warnings: list[str] = field(default_factory=list)


def units_from_blocks(blocks: list[Block], ids: list[str]) -> list[Unit]:
    units = []
    for b, bid in zip(blocks, ids):
        text = b.text
        if b.type == "image":
            text = f"[图]{(' ' + text) if text else ''}"
        elif b.type == "table":
            text = f"[表] {re.sub(r'<[^>]+>', ' ', text)}".strip()
        if text:
            units.append(Unit(bid, b.seq, b.page, b.type, text))
    return units


# ---------------- 文本工具 ----------------

def join_lines(lines: list[str]) -> str:
    """拼接行文本：中文之间不加空格，小问标记前换行，其余补一个空格。"""
    out = ""
    for line in (raw.strip() for raw in lines):
        if not line:
            continue
        if not out:
            out = line
        elif (SUB_MARK_RE.match(line) or OPTION_LINE_RE.match(line) or line.startswith("$$") or out.endswith("$$")
              or line.startswith(IMAGE_MARK)):
            out += "\n" + line
        elif _CJK.match(out[-1]) or _CJK.match(line[0]):
            out += line
        else:
            out += " " + line
    return out


def split_options(text: str) -> tuple[str, list[str]]:
    """从题干中切出 A. B. C. D. 选项；至少识别到 A、B 两项才算有选项。"""
    positions: list[tuple[int, int]] = []
    pos = 0
    for letter in "ABCDEFGH":
        # 选项字母前不能是字母或数字（排除 AB．、2A．），但可以紧贴汉字：「的是A．钢铁生锈B．…」
        m = re.compile(rf"(?<![A-Za-z0-9])({letter})\s*[.．、:：]").search(text, pos)
        if not m:
            break
        positions.append((m.start(1), m.end()))
        pos = m.end()
    if len(positions) < 2:
        return text, []
    stem = text[: positions[0][0]].strip()
    opts = []
    for i, (_, end) in enumerate(positions):
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        opts.append(re.sub(r"\s+", " ", text[end:nxt]).strip())
    return stem, opts


def split_inline_answer(text: str) -> tuple[str, str | None, str | None]:
    """拆出【答案】【解析】等标记，返回 (题干, 答案, 解析)。"""
    parts = INLINE_MARK_RE.split(text)
    if len(parts) == 1:
        return text, None, None
    stem, answer, analysis = parts[0], [], []
    for i in range(1, len(parts) - 1, 2):
        (answer if parts[i] == "答案" else analysis).append(parts[i + 1].strip())
    return stem, "\n".join(a for a in answer if a) or None, "\n".join(a for a in analysis if a) or None


def split_answer_text(text: str) -> tuple[str | None, str | None]:
    """把一道题的答案区文本拆为 (答案, 解析)。"""
    _, ans, ana = split_inline_answer(text)
    if ans or ana:
        return ans, ana
    m = CHOICE_ANSWER_RE.match(text)
    if m and (m.end() == len(text) or not re.match(r"[A-Za-z]", text[m.end():m.end() + 1])):
        letters = re.sub(r"[^A-H]", "", m.group(1))
        rest = text[m.end():].strip()
        return letters, rest or None
    text = text.strip()
    return (text or None), None


# 一段答案里依次写着多道题的答案，如「【1 题答案】【答案】C【2 题答案】【答案】A…」（MinerU 常把整页答案识别为一段）
NUMBERED_ANSWER_RE = re.compile(r"[【\[]\s*第?\s*(\d{1,3})\s*题\s*(?:答案|解析|详解)?\s*[】\]]")


# 解析中的最终答案：「故选：B」「故答案为：$\\sqrt{2}$.」
CHOSEN_RE = re.compile(r"故选\s*[:：]?\s*([A-H](?:\s*[,，、]?\s*[A-H]){0,7})(?![A-Za-z])")
FINAL_ANSWER_RE = re.compile(r"故答案为\s*[:：]\s*(.+?)\s*(?:[.。](?=\s|$|【)|\n|$)")
# 答案表格（表格单元去掉标签后的文本）：「题号 1 2 3 选项 A C D」，可有多组
TABLE_KEY_RE = re.compile(r"题号\s+((?:\d{1,3}\s+)+)(?:选项|答案)\s+((?:[A-H]{1,4}(?:\s+|$))+)")


def answer_from_analysis(analysis: str) -> str | None:
    if m := CHOSEN_RE.search(analysis):
        return re.sub(r"[^A-H]", "", m.group(1))
    if m := FINAL_ANSWER_RE.search(analysis):
        return m.group(1).strip() or None
    return None


def table_answer_keys(units: list[Unit]) -> dict[int, str]:
    keys: dict[int, str] = {}
    for u in units:
        if u.type != "table":
            continue
        for m in TABLE_KEY_RE.finditer(u.text):
            nos, answers = m.group(1).split(), m.group(2).split()
            if len(nos) == len(answers):
                keys.update((int(n), a) for n, a in zip(nos, answers))
    return keys


def numbered_answer_keys(units: list[Unit]) -> dict[int, tuple[str | None, str | None]]:
    """单元内依次标着多道题的答案（「【1 题答案】【答案】C【2 题答案】…」）：按题号拆出 (答案, 解析)。"""
    keys: dict[int, tuple[str | None, str | None]] = {}
    for u in units:
        marks = list(NUMBERED_ANSWER_RE.finditer(u.text))
        if len(marks) < 2:
            continue
        for i, m in enumerate(marks):
            part = u.text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(u.text)].strip()
            if part:
                keys.setdefault(int(m.group(1)), split_answer_text(part))
    return keys


def finalize_answers(questions: list[Question], units: list[Unit]) -> None:
    """拆题后统一整理答案：合在一段里的多题答案按题号分开；答案表格、带题号的答案段按题号填入；
    只有解析时从解析中取出最终答案。只填写空着的答案，不覆盖已关联的。"""
    distribute_numbered_answers(questions)
    table = table_answer_keys(units)
    numbered = numbered_answer_keys(units)
    for q in questions:
        if q.answer:
            continue
        if q.printed_no in table:
            q.answer = table[q.printed_no]
        elif q.printed_no in numbered:
            q.answer, q.analysis = numbered[q.printed_no][0], q.analysis or numbered[q.printed_no][1]
        if not q.answer and q.analysis:
            q.answer = answer_from_analysis(q.analysis)


def distribute_numbered_answers(questions: list[Question]) -> None:
    """答案（或解析）中标明其他题号的部分，分给对应题号、且该项还空着的题；答案与解析分别处理。"""
    by_no = {q.printed_no: q for q in questions if q.printed_no is not None}
    for q in questions:
        for name in ("answer", "analysis"):
            text = getattr(q, name) or ""
            marks = list(NUMBERED_ANSWER_RE.finditer(text))
            if not any(int(m.group(1)) != q.printed_no for m in marks):
                continue
            own = [text[:marks[0].start()]]
            for i, m in enumerate(marks):
                part = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
                target = by_no.get(int(m.group(1)))
                if target is None or target is q or getattr(target, name):
                    own.append(part)  # 找不到对应的题或对方已有：留在原题
                elif name == "answer":
                    ans, ana = split_answer_text(part.strip())
                    target.answer, target.analysis = ans, target.analysis or ana
                else:
                    target.analysis = part.strip() or None
            setattr(q, name, "\n".join(p.strip() for p in own if p.strip()) or None)


def parse_answer_section(units: list[Unit]) -> dict[int, tuple[str | None, str | None, list[str]]]:
    """卷末答案区：支持「1-5 ACBDA」紧凑写法与逐题写法。返回 {题号: (答案, 解析, unit_ids)}。"""
    result: dict[int, tuple[str | None, str | None, list[str]]] = {}
    cur_no: int | None = None
    buf: list[str] = []
    buf_ids: list[str] = []

    def flush():
        if cur_no is not None and buf:
            ans, ana = split_answer_text(join_lines(buf))
            result[cur_no] = (ans, ana, list(buf_ids))

    for u in units:
        compact = list(COMPACT_ANSWER_RE.finditer(u.text))
        if compact:
            flush()
            cur_no, buf, buf_ids = None, [], []
            for m in compact:
                start, end = int(m.group(1)), int(m.group(2))
                letters = re.findall(r"[A-H]", m.group(3))
                if end - start + 1 == len(letters):
                    for i, letter in enumerate(letters):
                        result[start + i] = (letter, None, [u.id])
            continue
        m = Q_START_RE.match(u.text)
        if m:
            flush()
            cur_no, buf, buf_ids = int(m.group(1)), [u.text[m.end():]], [u.id]
        elif cur_no is not None:
            buf.append(u.text)
            buf_ids.append(u.id)
    flush()
    return result


def infer_type(section_type: str | None, options: list[str], stem: str, answer: str | None) -> str:
    multi = bool(answer and re.fullmatch(r"[A-H]{2,}", answer))
    if section_type:
        if section_type == "单选题" and multi:
            return "多选题"
        return section_type
    if options:
        return "多选题" if multi else "单选题"
    if BLANK_RE.search(stem):
        return "填空题"
    return "解答题"


# ---------------- 规则切分 ----------------

@dataclass
class _Draft:
    printed_no: int | None
    section: int
    section_type: str | None
    section_score: float | None
    unit_ids: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    gap: bool = False


def _classify_line(u: Unit) -> tuple[str, object]:
    """判断一个单元是答案区标题 / 大题标题 / 题号起始 / 普通内容。"""
    t = u.text
    if len(t) <= 30 and ANSWER_SECTION_RE.match(t):
        return "answers", None
    if NOTICE_RE.match(t):
        return "notice", None
    m = SECTION_RE.match(t)
    if m and "题" in t and len(t) <= 80:
        qtype = next((q for rx, q in SECTION_TYPES if rx.search(t)), None)
        each = SCORE_EACH_RE.search(t)
        return "section", (qtype, float(each.group(1)) if each else None)
    m = Q_START_RE.match(t)
    if m:
        return "question", int(m.group(1))
    return "content", None


def rule_segment(units: list[Unit], *, with_answer: bool) -> Segmentation:
    drafts: list[_Draft] = []
    preamble: list[Unit] = []
    answer_units: list[Unit] = []
    section, section_type, section_score = 0, None, None
    last_no: int | None = None
    in_answers = False
    # 大题标题之后、下一题之前的内容视为标题续行（折行的说明文字），不能接到上一题末尾
    header_text: str | None = None
    # 「注意事项」中的 1．2．不是题目，直到出现第一个大题标题
    in_notice = False

    for u in units:
        if in_answers:
            answer_units.append(u)
            continue
        kind, val = _classify_line(u)
        if kind == "answers" and drafts:
            in_answers = True
            continue
        if kind == "notice" and not drafts:
            in_notice = True
            preamble.append(u)
            continue
        if in_notice and kind != "section":
            preamble.append(u)
            continue
        if kind == "section":
            in_notice = False
            section += 1
            section_type, section_score = val  # type: ignore[misc]
            header_text = u.text
            if not drafts:
                preamble.append(u)
            continue
        if kind == "content" and header_text is not None:
            # 「每小题 5 分」可能被折到下一行，拼接后重新识别
            header_text += u.text
            section_type = section_type or next((q for rx, q in SECTION_TYPES if rx.search(header_text)), None)
            if section_score is None and (each := SCORE_EACH_RE.search(header_text)):
                section_score = float(each.group(1))
            if not drafts:
                preamble.append(u)
            continue
        if kind == "question":
            n = val  # type: ignore[assignment]
            # 首题可以不从 1 开始（只上传了部分页），此时标记为题号不连续
            accept = last_no is None or n == last_no + 1 or n == 1 or last_no < n <= last_no + 3
            if accept:
                d = _Draft(n, section, section_type, section_score,
                           gap=last_no is not None and n not in (last_no + 1, 1) or last_no is None and n != 1)
                d.unit_ids.append(u.id)
                d.lines.append(Q_START_RE.sub("", u.text, count=1))
                drafts.append(d)
                last_no = n
                header_text = None
                continue
        if drafts:
            drafts[-1].unit_ids.append(u.id)
            drafts[-1].lines.append(u.text)
        else:
            preamble.append(u)

    answers = parse_answer_section(answer_units) if with_answer and answer_units else {}
    questions = [_build(d.printed_no, d.lines, d.unit_ids, d.section, d.section_type, d.section_score,
                        answers.get(d.printed_no) if d.printed_no else None, with_answer, gap=d.gap)
                 for d in drafts]
    if with_answer:
        finalize_answers(questions, units)
    return Segmentation(questions=questions, preamble=preamble)


def _strip_image_marks(text: str) -> str:
    """配图单独保存并在题干下方展示，文本中的占位符去掉。"""
    text = text.replace(IMAGE_MARK, "")
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", text).strip()


def _build(printed_no, lines, unit_ids, section, section_type, section_score, section_answer,  # noqa: ANN001
           with_answer: bool, *, gap: bool = False, llm_type: str | None = None, llm_score: float | None = None) -> Question:
    text = join_lines(lines)
    flags: list[str] = []
    m = Q_SCORE_RE.match(text)
    q_score = float(m.group(1)) if m else None
    if m:
        text = text[m.end():]
    stem, ans, ana = split_inline_answer(text)
    stem, options = split_options(stem)
    stem = _strip_image_marks(stem)
    options = [_strip_image_marks(o) for o in options]
    if section_answer and not ans:
        ans, ana = section_answer[0], section_answer[1] or ana
    if not with_answer:
        ans = ana = None

    qtype = llm_type if llm_type in QTYPES else infer_type(section_type, options, stem, ans)
    if qtype in ("单选题", "多选题") and not options:
        flags.append("选择题未识别到选项")
    score = llm_score or q_score or section_score
    if not score:
        score = DEFAULT_SCORE[qtype]
        flags.append("分值为默认值")

    conf = 0.95
    if gap:
        conf -= 0.2
        flags.append("题号不连续")
    if qtype == "单选题" and options and len(options) != 4:
        conf -= 0.2
        flags.append(f"单选题选项数为 {len(options)}")
    if qtype in ("单选题", "多选题") and not options:
        conf -= 0.25
    if len(stem.strip()) < 6:
        conf -= 0.3
        flags.append("题干过短")
    if "分值为默认值" in flags:
        conf -= 0.05
    if with_answer and not ans:
        conf -= 0.1
    if "�" in stem:
        conf -= 0.3
        flags.append("存在无法识别的字符")

    return Question(printed_no, qtype, float(score), stem.strip(), options, ans, ana, list(unit_ids),
                    round(max(0.3, min(0.99, conf)), 2), section, flags)


# ---------------- 大模型修正 ----------------

LLM_SYSTEM = """你是中国中小学试卷的结构化助手。输入是一份试卷按阅读顺序排列的版面单元（id、页码、文本），
可能含 OCR 噪声。请把单元分组为题目，只输出 JSON，不要改写任何文本。

规则：
1. 每道题包含题号、题干、选项、小问（（1）（2）等小问属于同一道题，不要拆开）以及紧跟在题目内的配图。
2. 试卷标题、考试说明、大题标题（如「一、单选题：本题共 8 小题，每小题 5 分」）不属于任何题。
3. 卷末的参考答案 / 解析区单元放到对应题目的 answer_units 中，不要放进 units。答案区常见形式：按题号重新印出题干，
   后接【分析】【解答】【点评】或「故选」「故答案为」——重印的题干和这些解析都放进该题的 answer_units；
   答案表格（如「题号 1 2 3 / 选项 A C D」）放进表中第一题的 answer_units。
4. 每个单元最多属于一道题；题目按原卷顺序输出；units 内保持原顺序。
5. type 只能是：单选题、多选题、填空题、解答题；score 为该题分值（数字，未知填 0）。

输出格式：
{"questions":[{"no":1,"type":"单选题","score":5,"units":["u3","u4"],"answer_units":["u80"]}]}"""


def _llm_payload(units: list[Unit]) -> str:
    rows = [{"id": u.id, "p": u.page, "t": u.text[:300]} for u in units]
    return json.dumps(rows, ensure_ascii=False)


# 大模型分组中可以自动修正的问题数上限：超过则整体退回规则结果
MAX_LLM_REPAIRS = 3
# 大模型分组至少要覆盖规则识别出的题目单元的比例
MIN_LLM_COVERAGE = 0.6


def _validate_llm(data: object, known: dict[str, Unit]) -> tuple[list[dict], int]:
    """校验大模型分组并就地修正小问题：不存在的单元、重复使用的单元（保留第一次）丢弃，没有单元的题跳过，
    顺序按原卷重排。返回 (分组, 修正处数)；分组中 repaired 为 True 的题需要老师核对。"""
    questions = items_of(data, "questions")
    if not questions:
        raise ValueError("缺少 questions")
    seen: set[str] = set()
    out: list[dict] = []
    repairs = 0
    for q in questions:
        if not isinstance(q, dict):
            repairs += 1
            continue
        fixed = False
        picked: dict[str, list[str]] = {"units": [], "answer_units": []}
        for key, dst in picked.items():
            raw = q.get(key) or []
            for i in raw if isinstance(raw, list) else []:
                if isinstance(i, str) and i in known and i not in seen:
                    seen.add(i)
                    dst.append(i)
                else:
                    fixed = True
        if not picked["units"]:
            repairs += 1
            continue
        repairs += fixed
        ids = sorted(picked["units"], key=lambda i: known[i].seq)
        ans_ids = sorted(picked["answer_units"], key=lambda i: known[i].seq)
        out.append({"type": q.get("type"), "score": q.get("score"), "units": ids, "answer_units": ans_ids,
                    "repaired": fixed})
    if not out:
        raise ValueError("没有可用的题目")
    ordered = sorted(out, key=lambda g: known[g["units"][0]].seq)
    if ordered != out:
        repairs += 1
    if repairs > MAX_LLM_REPAIRS:
        raise ValueError(f"分组问题过多（{repairs} 处）")
    return ordered, repairs


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


async def segment(units: list[Unit], settings: Settings, *, with_answer: bool) -> Segmentation:
    rule = rule_segment(units, with_answer=with_answer)
    if not settings.llm_enabled:
        rule.warnings.append("未配置大模型，仅使用规则拆题")
        return rule
    if len(units) > settings.llm_max_units:
        rule.warnings.append(f"版面单元过多（{len(units)}），仅使用规则拆题")
        return rule

    known = {u.id: u for u in units}
    try:
        data = await chat_json(LLM_SYSTEM, _llm_payload(units), settings, purpose="segment")
        groups, repairs = _validate_llm(data, known)
        rule_ids = {i for q in rule.questions for i in q.unit_ids}
        got = {i for g in groups for i in g["units"] + g["answer_units"]}
        if rule_ids and len(rule_ids & got) < MIN_LLM_COVERAGE * len(rule_ids):
            raise ValueError("分组遗漏了大部分题目内容")
    except (LLMError, ValueError) as e:
        log.warning("大模型拆题结果不可用：%s", e)
        rule.warnings.append(f"大模型拆题失败，已使用规则结果：{e}")
        return rule

    # 规则结果提供大题信息（类型、每题分值），按首个单元归属到对应大题
    rule_by_unit = {uid: q for q in rule.questions for uid in q.unit_ids}
    questions: list[Question] = []
    for g in groups:
        ref = next((rule_by_unit[i] for i in g["units"] if i in rule_by_unit), None)
        # 版面顺序错乱时（如图中文字标签排在题号前），把带题号的单元提到最前
        head = next((k for k, i in enumerate(g["units"][:4]) if Q_START_RE.match(known[i].text)), 0)
        if head:
            g["units"].insert(0, g["units"].pop(head))
        lines = [known[i].text for i in g["units"]]
        m = Q_START_RE.match(lines[0])
        printed_no = int(m.group(1)) if m else None
        if m:
            lines[0] = lines[0][m.end():]
        section_answer = None
        if g["answer_units"]:
            ans_lines = [known[i].text for i in g["answer_units"]]
            ans_lines[0] = Q_START_RE.sub("", ans_lines[0], count=1)
            section_answer = (*split_answer_text(join_lines(ans_lines)), g["answer_units"])
        elif ref and ref.answer:
            section_answer = (ref.answer, ref.analysis, [])
        score = g["score"] if isinstance(g["score"], (int, float)) and g["score"] > 0 else None
        rule_score = ref.score if ref and "分值为默认值" not in ref.flags else None
        # 答案区单元不计入题目区域，避免「查看原图」框到卷末答案
        q = _build(printed_no, lines, g["units"], ref.section if ref else 0, None, None, section_answer, with_answer,
                   llm_type=g["type"] if g["type"] in QTYPES else (ref.type if ref else None),
                   llm_score=score or rule_score)
        # 与规则结果一致则略微提升置信度，不一致或经过自动修正则进入待核对
        if g["repaired"]:
            q.confidence = round(max(0.3, q.confidence - 0.25), 2)
            q.flags.append("大模型分组有误，已自动修正")
        elif ref and _overlap(ref.unit_ids, g["units"]) >= 0.8:
            q.confidence = round(min(0.99, q.confidence + 0.03), 2)
        else:
            q.confidence = round(max(0.3, q.confidence - 0.25), 2)
            q.flags.append("规则与大模型切分不一致")
        questions.append(q)

    if with_answer:
        finalize_answers(questions, units)
    warnings = rule.warnings + ([f"大模型拆题有 {repairs} 处分组问题，已自动修正，相关题目已标记待核对"] if repairs else [])
    return Segmentation(questions=questions, preamble=rule.preamble, used_llm=True, warnings=warnings)
