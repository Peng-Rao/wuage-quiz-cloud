"""试卷分类：先用规则从标题中提取，再（可选）由大模型补全和校正。"""

import logging
import re

from ..config import Settings
from ..schemas import PaperMeta
from .llm import LLMError, chat_json

log = logging.getLogger(__name__)

STAGES = {
    "小学": ["语文", "数学", "英语", "科学", "道德与法治"],
    "初中": ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "道德与法治"],
    "高中": ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "信息技术"],
}
ALL_SUBJECTS = ["道德与法治", "信息技术", "语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "科学"]
PAPER_TYPES = ["期中考试", "期末考试", "月考", "单元测试", "模拟考试", "联考", "高考真题", "中考真题"]
GRADES = {
    "小学": ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级"],
    "初中": ["初一", "初二", "初三"],
    "高中": ["高一", "高二", "高三"],
}

_GRADE_ALIASES = [
    (r"高一", "高中", "高一"), (r"高二", "高中", "高二"), (r"高三", "高中", "高三"),
    (r"初一|七年级", "初中", "初一"), (r"初二|八年级", "初中", "初二"), (r"初三|九年级", "初中", "初三"),
] + [(rf"{c}年级", "小学", f"{c}年级") for c in "一二三四五六"]
_PAPER_TYPE_RULES = [
    (r"普通高等学校招生|高考", "高考真题"), (r"(?<!期)中考|学业水平考试", "中考真题"),
    (r"期中|半期", "期中考试"), (r"期末", "期末考试"), (r"月考|月测|质量检测", "月考"),
    (r"[一二三]模|模拟|适应性", "模拟考试"), (r"联考", "联考"), (r"单元", "单元测试"),
]
_TEXTBOOK_RE = re.compile(r"(人教[AB]?版|北师大版|苏教版|湘教版|沪教版|浙教版|鲁教版|冀教版)")
_YEAR_RE = re.compile(r"(20\d{2})\s*[—\-–~～至]+\s*(20\d{2})\s*学年")
# 学期可能紧跟学年，也可能在年级之后：「学年第一学期」「学年高一上学期」
_TERM_RE = re.compile(r"(上|下|第一|第二)\s*学期")
_PROVINCE_RE = re.compile(r"([一-龥]{2,3}?)(?:省|市|自治区)")
_DISTRICT_RE = re.compile(r"(?:省|市)([一-龥]{2,3}?)(?:区|县|市)")


def rule_classify(text: str) -> PaperMeta:
    # 标题常把科目写成「数 学」，先去掉汉字之间的空白
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    meta = PaperMeta()
    for pat, stage, grade in _GRADE_ALIASES:
        if re.search(pat, text):
            meta.stage, meta.grade = stage, grade
            break
    if not meta.stage:
        if re.search(r"高考|高中", text):
            meta.stage = "高中"
        elif re.search(r"(?<!期)中考|初中", text):
            meta.stage = "初中"
        elif "小学" in text:
            meta.stage = "小学"
    meta.subject = next((s for s in ALL_SUBJECTS if s in text), "")
    meta.paper_type = next((t for pat, t in _PAPER_TYPE_RULES if re.search(pat, text)), "")
    if m := _YEAR_RE.search(text):
        t = _TERM_RE.search(text, m.end())
        term = {"上": " 上", "第一": " 上", "下": " 下", "第二": " 下"}.get(t.group(1), "") if t else ""
        meta.school_year = f"{m.group(1)}—{m.group(2)}{term}"
    if m := _PROVINCE_RE.search(text):
        region = m.group(1)
        if d := _DISTRICT_RE.search(text[m.start():]):
            region += f" · {d.group(1)}"
        meta.region = region
    if m := _TEXTBOOK_RE.search(text):
        meta.textbook = m.group(1)
    return meta


LLM_SYSTEM = f"""你是中国中小学试卷分类助手。根据试卷开头的文字判断试卷属性，只输出 JSON：
{{"stage":"","subject":"","grade":"","paperType":"","region":"","schoolYear":"","textbook":""}}
- stage 只能是：{"、".join(STAGES)}
- subject 必须是该学段的学科：{"; ".join(f"{k}：{'、'.join(v)}" for k, v in STAGES.items())}
- grade 如：高一、初二、五年级；paperType 只能是：{"、".join(PAPER_TYPES)}
- region 如「北京 · 海淀」；schoolYear 如「2026—2027 上」；textbook 如「人教A版（2019）」
- 无法判断的字段留空字符串，不要猜测。"""


def _valid(meta: PaperMeta) -> PaperMeta:
    if meta.stage not in STAGES:
        meta.stage = ""
    if meta.subject and meta.stage and meta.subject not in STAGES[meta.stage]:
        meta.subject = ""
    if meta.grade and meta.stage and meta.grade not in GRADES[meta.stage]:
        meta.grade = ""
    if meta.paper_type not in PAPER_TYPES:
        meta.paper_type = ""
    return meta


async def classify(head_text: str, settings: Settings) -> tuple[PaperMeta, list[str]]:
    meta = _valid(rule_classify(head_text))
    warnings: list[str] = []
    if not settings.llm_enabled:
        return meta, warnings
    try:
        data = await chat_json(LLM_SYSTEM, head_text[:3000], settings, purpose="classify")
        llm = _valid(PaperMeta.model_validate({k: str(v or "").strip() for k, v in (data or {}).items()}))
    except (LLMError, ValueError) as e:
        log.warning("大模型分类失败：%s", e)
        warnings.append(f"大模型分类失败，已使用规则结果：{e}")
        return meta, warnings
    # 大模型结果优先，其为空的字段保留规则结果
    merged = {k: getattr(llm, k) or getattr(meta, k) for k in PaperMeta.model_fields}
    return _valid(PaperMeta(**merged)), warnings
