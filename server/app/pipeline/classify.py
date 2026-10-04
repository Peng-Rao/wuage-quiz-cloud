"""试卷分类：先用规则从标题中提取，再（可选）由大模型补全和校正。"""

import logging
import re
import unicodedata
from pathlib import Path

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
# 地名从词首或「2026年」之后开始，避免截出「年福建」
_PROVINCE_RE = re.compile(r"(?:(?<![一-龥])|(?<=年))(?!年)([一-龥]{2,3}?)(?:省|市|自治区)")
_DISTRICT_RE = re.compile(r"(?:省|市)([一-龥]{2,3}?)(?:区|县|市)")


_NOT_PLACE = r"[的在各本该我全某所城]"
# 学校名以这些词结尾：「集美中学」「外国语学校」「厦大附中」「厦门六中」（数字+中，不含「中考」）
_SCHOOL_END_RE = re.compile(r"中学|小学|学校|附中|[一二三四五六七八九十\d]{1,2}中(?![学考])")
_SCHOOL_BRANCH_RE = re.compile(r"^(?:[一-龥]{0,8}?(?:附属(?:中学|小学|学校)|分校|校区))")
# 学校名前面常连着的学年、年级、学期，逐个去掉
_SCHOOL_PREFIX_RE = re.compile(r"^(?:学年度?|年度?|届|第[一二]学期|[上下]学期|[一二三四五六七八九]年级|初[一二三]|高[一二三])")
# 学校名中的省份、「市」和区县：「福建省厦门市思明区双十中学」→「厦门双十中学」
_SCHOOL_PROVINCE_RE = re.compile(r"^[一-龥]{2,3}?省")
_SCHOOL_CITY_RE = re.compile(r"^([一-龥]{2,3}?)市")
_SCHOOL_DISTRICT_RE = re.compile(r"^([一-龥]{2,3}?)[区县]")
# 去掉区县后剩下这样的通用名称时保留区县名，避免「湖里区实验中学」变成另一所学校「厦门实验中学」
_GENERIC_SCHOOL_RE = re.compile(r"^(?:第|实验|[一二三四五六七八九十\d])")
_TITLE_RE = re.compile(r"试卷|试题|考试|测试|测验|练习|月考|期中|期末|联考|模拟|检测|真题|押题")
_XIAMEN_CONTEXT_RE = re.compile(r"厦门|思明区|湖里区|集美区|海沧区|同安区|翔安区")
_XIAMEN_SCHOOLS = (
    "双十中学", "第一中学", "一中", "第二中学", "二中", "第三中学", "三中", "第六中学", "六中",
    "第九中学", "九中", "第十中学", "十中", "第十一中学", "十一中", "同安第一中学", "同安一中",
    "翔安第一中学", "翔安一中", "槟榔中学", "松柏中学", "湖滨中学", "湖里中学", "集美中学", "大同中学", "诚毅中学",
    "莲花中学", "灌口中学", "上塘中学", "内厝中学", "华侨中学", "外国语学校", "实验中学",
    "湖里实验中学", "尚文实验学校", "金尚中学", "金林湾实验学校", "金鹰学校", "音乐学校",
    "观音山音乐学校", "大学附属科技中学", "五缘实验学校", "五缘第二实验学校",
    "瑞景外国语中学", "瑞景外国语学校", "科技中学", "高新中学",
)
_SCHOOL_ALIASES = {
    "厦门明区厦门市莲花中学": "厦门莲花中学",
    "厦门湖里五缘第二实验学校": "厦门五缘第二实验学校",
    "厦门第一中学集美分校": "厦门灌口中学",
    "厦门第一中学集美分校(灌口中学)": "厦门灌口中学",
    "厦门灌口中学(厦门一中集美分校)": "厦门灌口中学",
    "厦门双十": "厦门双十中学",
    "厦门外国语": "厦门外国语学校",
    "厦门瑞景外国语学校": "厦门瑞景外国语中学",
    "厦门外国语学校瑞景分校": "厦门瑞景外国语中学",
}
_NON_SCHOOLS = {"义务教育学校", "普通高等学校", "初级中学", "高级中学"}


def rule_title(text: str) -> str:
    """卷首标题：前几行中第一条像试卷名称的行（排除说明、页码等）。"""
    for line in [ln.strip() for ln in text.splitlines()][:8]:
        if 6 <= len(line) <= 60 and _TITLE_RE.search(line) and not re.search(r"^试卷第|共\s*\d+\s*页|注意事项|本试卷", line):
            return line
    return ""


def rule_school(text: str) -> str:
    """从试卷名称中取命题学校，如「2023-2024学年初三（上）厦门集美中学第二次月考英语」→「厦门集美中学」；联考、统考等没有学校的返回空。"""
    for part in re.finditer(r"[一-龥]+", text):
        run = part.group()
        if not (m := _SCHOOL_END_RE.search(run)):
            continue
        name = run[:m.end()]
        if branch := _SCHOOL_BRANCH_RE.match(run[m.end():]):
            name += branch.group()
        elif bracket := re.match(r"\s*[（(]([一-龥]{1,8}(?:分校|校区))[）)]", text[part.start() + m.end():]):
            name += bracket.group(1)
        while (p := _SCHOOL_PREFIX_RE.match(name)) and p.end() < len(name):
            name = name[p.end():]
        if len(name) >= 3 and name not in ("初中", "高中"):
            if school := clean_school(name, context=text):
                return school
    return ""


def clean_school(name: str, *, context: str = "") -> str:
    """统一地区写法与明确的学校别名；缺少城市时须有地区或试卷名称佐证，分校、附属校保留。"""
    name = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name))
    name = re.sub(r"\(([^()中学]{1,8}(?:分校|校区))\)", r"\1", name)
    if name in _NON_SCHOOLS:
        return ""
    if "厦门" in name:
        name = re.sub(r"^(?:福建(?:省)?)+", "", name)
    name = _SCHOOL_PROVINCE_RE.sub("", name)
    city = ""
    if name.startswith("厦门"):
        city = "厦门"
        name = re.sub(r"^(?:厦门(?:市)?)+", "", name)
    # 标题可能重复写「厦门市集美区厦门市集美区…」，逐段去掉行政前缀。
    while True:
        if m := _SCHOOL_CITY_RE.match(name):
            city, name = m.group(1), name[m.end():]
        elif (m := _SCHOOL_DISTRICT_RE.match(name)) and len(rest := name[m.end():]) >= 3:
            if _GENERIC_SCHOOL_RE.match(rest):
                name = m.group(1) + rest
                break
            name = rest
        else:
            break
    name = city + name
    name = re.sub(r"^(?:厦门)+", "厦门", name)
    if name.removeprefix(city or "厦门") in _NON_SCHOOLS:
        return ""
    if not name.startswith("厦门") and _XIAMEN_CONTEXT_RE.search(context):
        if any(name == s or (name.startswith(s) and _SCHOOL_BRANCH_RE.fullmatch(name[len(s):]))
               for s in _XIAMEN_SCHOOLS):
            name = "厦门" + name
    name = re.sub(r"^(厦门(?:同安|翔安)?)(?:第)?([一二三四五六七八九十]{1,3})(?:中学|中)"
                  r"(?=$|[一-龥]{0,8}(?:分校|校区|附属(?:学校|中学|小学)))",
                  r"\1第\2中学", name)
    return _SCHOOL_ALIASES.get(name, name)


def rule_classify(text: str, hint: str = "") -> PaperMeta:
    """hint 为上传时的文件名（去掉扩展名）：参与判断学段、学科等，不作为标题。"""
    title = rule_title(text)
    text = f"{text}\n{hint}" if hint else text
    # 标题常把科目写成「数 学」，先去掉汉字之间的空白
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    meta = PaperMeta(title=title)
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
    # 「所在的市（县、区）」等说明文字也会匹配，跳过含虚词的结果
    if m := next((m for m in _PROVINCE_RE.finditer(text) if not re.search(_NOT_PLACE, m.group(1))), None):
        region = m.group(1)
        if d := _DISTRICT_RE.search(text[m.start():]):
            region += f" · {d.group(1)}"
        meta.region = region
    if m := _TEXTBOOK_RE.search(text):
        meta.textbook = m.group(1)
    meta.school = clean_school(rule_school(title) or rule_school(hint), context=text)
    return meta


LLM_SYSTEM = f"""你是中国中小学试卷分类助手。根据试卷开头的文字判断试卷属性，只输出 JSON：
{{"title":"","stage":"","subject":"","grade":"","paperType":"","region":"","schoolYear":"","textbook":"","school":""}}
- stage 只能是：{"、".join(STAGES)}
- subject 必须是该学段的学科：{"; ".join(f"{k}：{'、'.join(v)}" for k, v in STAGES.items())}
- grade 如：高一、初二、五年级；paperType 只能是：{"、".join(PAPER_TYPES)}
- title 为试卷名称（卷首标题原文，去掉「绝密★启用前」等前缀）
- region 如「北京 · 海淀」；schoolYear 如「2026—2027 上」；textbook 如「人教A版（2019）」
- school 为命题学校名称，按卷首原文，去掉省份，如「厦门双十中学」「厦门六中」；保留分校、附属学校、校区的完整名称；联考、区统考等没有具体学校的留空
- 输入开头可能附有上传时的文件名，可作为学段、学科、类型、地区、年份的参考；title 以试卷正文为准
- 无法判断的字段留空字符串，不要猜测。"""


def _valid(meta: PaperMeta) -> PaperMeta:
    if meta.stage not in STAGES:
        meta.stage = ""
    if meta.subject and meta.stage and meta.subject not in STAGES[meta.stage]:
        # 学科通常直接写在标题里，比推断的学段可靠：保留学科，学段留空待核对
        meta.stage = ""
    if meta.grade and meta.stage and meta.grade not in GRADES[meta.stage]:
        meta.grade = ""
    if meta.paper_type not in PAPER_TYPES:
        meta.paper_type = ""
    meta.school = clean_school(meta.school, context=f"{meta.region} {meta.title}")
    return meta


async def classify(head_text: str, settings: Settings, *, file_name: str = "") -> tuple[PaperMeta, list[str]]:
    hint = Path(file_name).stem if file_name else ""
    meta = _valid(rule_classify(head_text, hint))
    warnings: list[str] = []
    if not settings.llm_enabled:
        return meta, warnings
    try:
        user = (f"文件名：{hint}\n\n" if hint else "") + head_text[:3000]
        data = await chat_json(LLM_SYSTEM, user, settings, purpose="classify")
        llm = _valid(PaperMeta.model_validate({k: str(v or "").strip() for k, v in (data or {}).items()}))
    except (LLMError, ValueError) as e:
        log.warning("大模型分类失败：%s", e)
        warnings.append(f"大模型分类失败，已使用规则结果：{e}")
        return meta, warnings
    # 大模型结果优先，其为空的字段保留规则结果
    merged = {k: getattr(llm, k) or getattr(meta, k) for k in PaperMeta.model_fields}
    return _valid(PaperMeta(**merged)), warnings
