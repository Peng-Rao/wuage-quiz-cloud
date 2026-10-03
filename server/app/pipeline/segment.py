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
    # 阅读材料：英语阅读 / 完形填空原文、语文阅读选文等，同一篇材料下的各题共用
    material: str | None = None
    material_unit_ids: list[str] = field(default_factory=list)


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


# 题内答案：解析版试卷在每题之后紧跟【答案】【解析】等，规则切分把它们算进该题，大模型则放在 answer_units
INLINE_ANSWER_RE = re.compile(r"^\s*(【(答案|解析|分析|详解|点评|解答|小问\d*详解|知识点|考点)】|故选|故答案为)")


def question_part(unit_ids: list[str], text_of) -> list[str]:  # noqa: ANN001
    """规则切分的题目单元去掉题内答案部分（从第一个【答案】【解析】等开始），用于与大模型的题目单元比对。"""
    out: list[str] = []
    for i, uid in enumerate(unit_ids):
        if i and INLINE_ANSWER_RE.match(text_of(uid) or ""):
            break
        out.append(uid)
    return out


# ---------------- 阅读材料 ----------------
#
# 英语阅读理解、完形填空、任务型阅读，语文现代文 / 文言文 / 诗歌阅读等：一篇材料后接若干题。
# 材料不带题号，按题号切分时会被拼进上一题的选项或解析，或被当作大题说明丢掉。
# 拆题前先识别出材料、从拆题输入中移出，拆题后挂到材料之后的各题上。

# 大题标题之外的分节标题：Ⅲ．阅读理解、第二节 完形填空、第一部分、Part II
ROMAN_HEAD_RE = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|(?:I{1,3}|IV|VI{0,3}|IX|X))\s*[.．、]")
PART_HEAD_RE = re.compile(r"^\s*(?:第\s*[一二三四五六七八九十\d]+\s*(?:节|部分)|(?:Part|Section)\s+[IVX\d]+\b)")
# 材料小标题：A、(B)、Passage C、Text 2
LABEL_RE = re.compile(r"^\s*(?:(?:[Pp]assage|[Tt]ext)\s*[A-H\d]|[(（]?[A-H][)）]?)\s*$")
# 材料前的说明：阅读下面短文…、从每小题所给的…选出可以填入空白处…；单独一行的分值说明
INSTRUCTION_RE = re.compile(
    r"阅读(?:下面|下列|以下|下文|短文|材料|文章|理解)|完形填空|根据(?:短文|文章|材料|对话)|填入空白处|[Rr]ead the (?:following|passage)")
SCORE_LINE_RE = re.compile(r"^\s*[（(][^（）()]*分[^（）()]*[)）]\s*$")
PAGE_NOISE_RE = re.compile(r"^\s*(?:第\s*\d+\s*页|\d+\s*/\s*\d+\s*$)")
# 答案、解析块：【答案】【解析】【原文】【导语】【32题详解】、故选…
ANSWER_BLOCK_RE = re.compile(r"^\s*(?:【[^】]{1,12}】|故选|故答案为)")
# 对话行、列表行（a. b. 排序题、1) 2)）不是材料段落
SPEAKER_RE = re.compile(r"^\s*(?:[—―–-]|[A-Z][a-z]{0,10}\s?[:：]|[a-h1-9]\s*[.．、)）])")
# 材料中不会有选项行；有则是题目内容，不能移出
OPTION_IN_TEXT_RE = re.compile(r"(?m)^\s*[A-D]\s*[.．、]")
# 含选项的单元（上一题到此结束，其后的英文段落可以是新材料）
HAS_OPTIONS_RE = re.compile(r"(?<![A-Za-z])[A-D]\s*[.．、]\s*\S")
# 材料正文至少的字数：大题说明的续行（「每小题 5 分」等）远短于此；
# 有小标题且带配图的材料（家谱图、海报配一两句说明）文字可以很短，但不能只有图（听力的图片选项）
MATERIAL_MIN_CHARS = 150
MATERIAL_MIN_CHARS_WITH_IMAGE = 30
# 一篇材料最多挂几道题（完形填空可达 15 空）；分节标题识别不到时避免挂到后面的题
MATERIAL_MAX_QUESTIONS = 15


@dataclass
class Material:
    text: str
    unit_ids: list[str]      # 材料正文的单元（含图、表），用于配图与原图区域
    removed: list[str]       # 移出拆题输入的单元：正文、小标题、说明行与页眉页脚
    begin: int               # 第一个单元（含小标题、说明）的 seq
    start: int               # 正文最后一个单元的 seq，其后的题挂上本材料
    end: int | None = None   # 下一个分节标题或下一篇材料的 seq


def _is_heading(t: str) -> bool:
    if len(t) > 80:
        return False
    if SECTION_RE.match(t) or PART_HEAD_RE.match(t):
        return True
    # 罗马数字也用作题内条目（I.用硼酸…合成 ZB。Ⅱ.受热…）：分节标题不以句号结尾、不含公式
    return bool(ROMAN_HEAD_RE.match(t)) and not re.search(r"[。.]$|\$", t)


WORD_RE = re.compile(r"[A-Za-z]{2,}")
MATH_SPAN_RE = re.compile(r"\$[^$]*\$")


def _is_prose(t: str) -> bool:
    """英文段落：较长、以英文单词为主，不是选项行、对话行或公式推导（无小标题的阅读材料靠它识别）。"""
    t = t.strip()
    if len(t) < 60 or OPTION_LINE_RE.match(t) or SPEAKER_RE.match(t) or "\\" in t:
        return False
    plain = MATH_SPAN_RE.sub("", t)
    if len(plain) < 0.9 * len(t) or len(WORD_RE.findall(plain)) < 10:
        return False
    letters = sum(c.isascii() and c.isalpha() for c in plain)
    return letters >= 0.6 * len(plain) and len(_CJK.findall(plain)) <= 0.08 * len(plain)


# 一行里同时有 A、B 两个选项：是题目（完形填空的「1. A. treasure B. surprise」），不是材料中的列表项
TWO_OPTIONS_RE = re.compile(r"(?<![A-Za-z])A\s*[.．、]\s*\S.*?(?<![A-Za-z])B\s*[.．、]\s*\S")


def _chars(body: list[Unit]) -> int:
    return sum(len(b.text) for b in body if b.type != "image")


def _list_item(body: list[Unit], n: int, last_no: int, text: str) -> bool:
    """材料正文中的编号列表（告示里的 1. 2.）：编号倒退，且正文已足够长、编号从 1 起或接着正文中的上一项。
    分节后题号重新从 1 编起时正文还很短，不受影响；带选项的是题目。"""
    if (n > last_no or TWO_OPTIONS_RE.search(text)
            or _chars(body) < MATERIAL_MIN_CHARS):
        return False
    return n == 1 or any(re.match(rf"\s*{n - 1}\s*[.．、]", b.text) for b in body)


def find_materials(units: list[Unit]) -> list[Material]:
    """按阅读顺序扫描单元：分节标题、材料小标题或说明行之后、下一题之前的正文是一篇材料；
    没有小标题时，上一题之后紧接的英文段落也是材料。答案解析块中的内容（如听力原文）不算。"""
    materials: list[Material] = []
    headings: list[int] = []
    anchors: list[Unit] = []        # 正文之前的小标题、说明、页眉页脚
    body: list[Unit] | None = None  # 正在收集的正文；None 表示未在收集
    pending = False                 # 已遇到小标题 / 说明 / 分节标题，等待正文
    after_heading = False           # 当前说明紧跟在分节标题之后（说明中的分值供大题使用，不移出）
    noise: list[Unit] = []          # 正文中的页眉页脚
    lead_images: list[Unit] = []    # 紧挨在正文前的配图
    # None 不在答案块中 / transcript 听力原文（其中的英文不是材料）/ analysis 解析
    in_answer: str | None = None
    # 上一题已结束（出现过选项或解析），其后的英文段落才可能是没有小标题的材料
    question_done = False
    last_no = 0

    def reset() -> None:
        nonlocal anchors, body, pending, after_heading, noise
        anchors, body, pending, after_heading, noise = [], None, False, False, []

    def confirm(body: list[Unit]) -> None:
        text = _strip_image_marks("\n".join(b.text for b in body if b.type != "image"))
        labeled_figure = any(LABEL_RE.match(a.text) for a in anchors) and any(b.type == "image" for b in body)
        if (len(text) < (MATERIAL_MIN_CHARS_WITH_IMAGE if labeled_figure else MATERIAL_MIN_CHARS)
                or OPTION_IN_TEXT_RE.search(text) or NOTICE_RE.match(text) or text.startswith("温馨提示")
                or any(SUB_MARK_RE.match(b.text.strip()) for b in body)):
            return
        removed = [a.id for a in anchors if not (after_heading and INSTRUCTION_RE.search(a.text))]
        materials.append(Material(text, [b.id for b in body], removed + [x.id for x in noise + body],
                                  begin=(anchors or body)[0].seq, start=body[-1].seq))

    for u in units:
        t = u.text.strip()
        if u.type == "image":
            if body is not None:
                body.append(u)
            elif pending:
                body = [u]
            else:
                lead_images.append(u)
            continue
        images, lead_images = lead_images, []
        m = Q_START_RE.match(t)
        if m and not (body is not None and _list_item(body, int(m.group(1)), last_no, t)):
            if body:
                confirm(body)
            reset()
            in_answer, last_no = None, int(m.group(1))
            question_done = bool(HAS_OPTIONS_RE.search(t))
            continue
        if _is_heading(t):
            headings.append(u.seq)
            reset()
            pending, after_heading, in_answer = True, True, None
            continue
        # 单独一行的分值说明只在分节标题之后才算（卷首的「时间：60 分钟，满分：100 分」不算）
        # 含选项的行即使带着说明文字（选项后粘着「Ⅲ. 阅读理解」）也是题目内容
        if LABEL_RE.match(t) or (pending and SCORE_LINE_RE.match(t)) or (
                len(t) <= 150 and INSTRUCTION_RE.search(t) and not OPTION_IN_TEXT_RE.search(t)):
            if body is not None and not LABEL_RE.match(t) and _chars(body) >= MATERIAL_MIN_CHARS:
                noise.append(u)  # 材料之后的「根据材料内容选择最佳答案」：随材料移出
                continue
            if body is not None:  # 正文之后又出现小标题：前面的不是材料
                reset()
            anchors.append(u)
            pending, in_answer = True, None
            continue
        if PAGE_NOISE_RE.match(t):
            if body is not None:
                noise.append(u)
            elif pending:
                anchors.append(u)
            continue
        if ANSWER_BLOCK_RE.match(t):
            reset()
            in_answer = "transcript" if "原文" in t[:8] else "analysis"
            continue
        if body is not None and (OPTION_IN_TEXT_RE.search(t) or TWO_OPTIONS_RE.search(t)):
            # 材料中不会有选项：是题目内容（如分节说明之后的选择题），放弃
            reset()
            question_done = True
            continue
        if body is not None:
            body.append(u)
        elif pending:
            body = [u]
        elif (last_no and in_answer != "transcript" and (question_done or in_answer == "analysis")
              and u.type == "text" and _is_prose(t)):
            body = images + [u]
            in_answer = None
        elif HAS_OPTIONS_RE.search(t):
            question_done = True

    for m in materials:
        nxt = [h for h in headings if h > m.start] + [x.begin for x in materials if x.begin > m.start]
        m.end = min(nxt) if nxt else None
    return materials


def attach_materials(questions: list[Question], materials: list[Material], units: list[Unit]) -> None:
    """材料之后、下一个分节标题或下一篇材料之前的题挂上该材料；完形填空等题干为空的题不再算作题干过短。"""
    seq = {u.id: u.seq for u in units}
    for m in materials:
        members = [q for q in questions if q.unit_ids and m.start < seq[q.unit_ids[0]]
                   and (m.end is None or seq[q.unit_ids[0]] < m.end)]
        for q in members[:MATERIAL_MAX_QUESTIONS]:
            q.material, q.material_unit_ids = m.text, list(m.unit_ids)
            if "题干过短" in q.flags:
                q.flags.remove("题干过短")
                q.confidence = round(min(0.99, q.confidence + 0.3), 2)


async def segment(units: list[Unit], settings: Settings, *, with_answer: bool) -> Segmentation:
    materials = find_materials(units)
    if materials:
        removed = {i for m in materials for i in m.removed}
        all_units, units = units, [u for u in units if u.id not in removed]
    seg = await _segment(units, settings, with_answer=with_answer)
    if materials:
        attach_materials(seg.questions, materials, all_units)
    return seg


async def _segment(units: list[Unit], settings: Settings, *, with_answer: bool) -> Segmentation:
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
        elif ref and _overlap(question_part(ref.unit_ids, lambda i: known[i].text if i in known else ""),
                              g["units"]) >= 0.8:
            q.confidence = round(min(0.99, q.confidence + 0.03), 2)
        else:
            q.confidence = round(max(0.3, q.confidence - 0.25), 2)
            q.flags.append("规则与大模型切分不一致")
        questions.append(q)

    if with_answer:
        finalize_answers(questions, units)
    warnings = rule.warnings + ([f"大模型拆题有 {repairs} 处分组问题，已自动修正，相关题目已标记待核对"] if repairs else [])
    return Segmentation(questions=questions, preamble=rule.preamble, used_llm=True, warnings=warnings)
