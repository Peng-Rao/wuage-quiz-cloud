import json

import httpx
import pytest

from app.config import Settings
from app.pipeline import llm
from app.pipeline.classify import rule_classify, rule_school
from app.pipeline.parsers.base import SourceFile
from app.pipeline.parsers.lite import LiteParser
from app.pipeline.segment import (
    Unit, parse_answer_section, rule_segment, segment, split_options, units_from_blocks,
)

from .fixtures import EXPECTED, TITLE, make_exam_pdf


@pytest.fixture
async def units() -> list[Unit]:
    doc = await LiteParser().parse(SourceFile("exam.pdf", make_exam_pdf(), "pdf"), ocr=False)
    return units_from_blocks(doc.blocks, [f"u{b.seq}" for b in doc.blocks])


def _summary(qs):  # noqa: ANN001
    return [(q.type, q.score, len(q.options), q.answer) for q in qs]


async def test_rule_segment_matches_expected(units):
    seg = rule_segment(units, with_answer=True)
    assert _summary(seg.questions) == [(t, float(s), n, a) for t, s, n, a in EXPECTED]
    q1 = seg.questions[0]
    assert q1.stem.startswith("已知集合") and not q1.stem.startswith("1")
    assert all(q.confidence >= 0.9 for q in seg.questions)
    # 大题标题折行的说明文字不能混进上一题的选项
    assert seg.questions[3].options[-1] == "6"
    # 解答题小问换行保留，便于拆分
    assert "\n（1）" in seg.questions[7].stem and "\n（2）" in seg.questions[7].stem
    assert TITLE in [u.text for u in seg.preamble]


async def test_rule_segment_without_answers(units):
    seg = rule_segment(units, with_answer=False)
    assert all(q.answer is None for q in seg.questions)


def test_split_options_requires_marker_boundary():
    stem, opts = split_options("设 A∩B 为集合，则（ ）\nA．1  B．2  C．3  D．4")
    assert stem == "设 A∩B 为集合，则（ ）" and opts == ["1", "2", "3", "4"]
    assert split_options("点 A.B 两点之间")[1] == []


def test_answer_section_formats():
    us = [Unit(f"a{i}", i, 3, "text", t) for i, t in enumerate([
        "1-3 ABD", "4．【答案】C【解析】由题意可得", "5．(1) 略", "(2) 3",
    ])]
    got = parse_answer_section(us)
    assert got[1][0] == "A" and got[3][0] == "D"
    assert got[4][:2] == ("C", "由题意可得")
    assert got[5][0].startswith("(1) 略")


def test_gap_in_numbering_lowers_confidence():
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate([
        "1．第一题的题干内容比较长", "2．第二题的题干内容比较长", "4．第四题的题干内容比较长",
    ])]
    seg = rule_segment(us, with_answer=False)
    assert [q.printed_no for q in seg.questions] == [1, 2, 4]
    assert seg.questions[2].confidence < 0.8 and "题号不连续" in seg.questions[2].flags


def test_rule_classify():
    meta = rule_classify(f"{TITLE}\n数 学（人教A版）")
    assert (meta.stage, meta.grade, meta.subject, meta.paper_type) == ("高中", "高一", "数学", "期中考试")
    assert meta.school_year == "2026—2027 上"
    assert meta.region == "北京 · 海淀"
    assert meta.textbook == "人教A版"
    assert rule_classify("2026 年广东省中考数学真题").paper_type == "中考真题"
    assert meta.school == ""


@pytest.mark.parametrize("title, school", [
    ("2023-2024学年初三（上）厦门集美中学第二次月考英语", "厦门集美中学"),
    ("厦门六中2022—2023学年高一年10月月考物理试卷", "厦门第六中学"),
    ("2022-2023学年福建省厦门大学附属科技中学高一（上）月考物理试卷（10月份）", "厦门大学附属科技中学"),
    ("厦门外国语学校2022-2023学年度第一学期期中考试", "厦门外国语学校"),
    ("2023学年高一上学期厦门一中期中考试", "厦门第一中学"),
    ("2024学年初中数学第一学期期中质量检测", ""),
    ("厦门市2025年中考数学", ""),
    ("2024年福建省厦门市思明区双十中学九年级上期中", "厦门双十中学"),
    ("厦门市第一中学2023届高三", "厦门第一中学"),
    ("厦门市湖里区实验中学2024年月考", "厦门湖里实验中学"),
    ("思明区莲花中学期末考试", "厦门莲花中学"),
])
def test_rule_school(title, school):
    assert rule_school(title) == school


# ---------- 大模型修正 ----------

def _llm_settings() -> Settings:
    return Settings(llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="m")


def _mock_llm(monkeypatch, handler):  # noqa: ANN001
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))


def _sse(content: str) -> httpx.Response:
    """按 SSE 分块返回，并夹带思考模型的 reasoning_content（不应计入结果）。"""
    events = [{"choices": [{"delta": {"reasoning_content": "先分析题号……"}}]}]
    events += [{"choices": [{"delta": {"content": content[i:i + 40]}}]} for i in range(0, len(content), 40)]
    body = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def _reply(obj) -> httpx.Response:  # noqa: ANN001
    return _sse(json.dumps(obj, ensure_ascii=False))


async def test_llm_grouping_used_when_valid(units, monkeypatch):
    rule = rule_segment(units, with_answer=True)
    groups = [{"no": i + 1, "type": q.type, "score": q.score, "units": q.unit_ids, "answer_units": []}
              for i, q in enumerate(rule.questions)]
    # 大模型把第 8、9 题合成一题：两者不一致，合并后的题应进入待核对
    groups[7]["units"] = groups[7]["units"] + groups[8]["units"]
    del groups[8]
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _reply({"questions": groups})

    _mock_llm(monkeypatch, handler)
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert seg.used_llm and len(seg.questions) == 8
    assert seen["body"]["response_format"] == {"type": "json_object"} and seen["body"]["stream"] is True
    assert seg.questions[0].confidence >= 0.95
    assert seg.questions[7].confidence < 0.8 and "规则与大模型切分不一致" in seg.questions[7].flags
    # 答案仍按规则结果关联
    assert seg.questions[4].answer == "BCD"


async def test_llm_bare_array_accepted(units, monkeypatch):
    # 部分模型（如 qwen3.8）省略外层对象，直接返回题目数组
    rule = rule_segment(units, with_answer=True)
    groups = [{"no": i + 1, "type": q.type, "score": q.score, "units": q.unit_ids, "answer_units": []}
              for i, q in enumerate(rule.questions)]
    _mock_llm(monkeypatch, lambda _: _reply(groups))
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert seg.used_llm and len(seg.questions) == 9 and not seg.warnings


@pytest.mark.parametrize("bad", [
    {"questions": [{"units": ["不存在的单元"]}]},
    {"questions": []},
    [],
    ["u1"],
    "不是 JSON",
])
async def test_llm_invalid_falls_back_to_rules(units, monkeypatch, bad):
    def handler(_: httpx.Request) -> httpx.Response:
        return _sse(bad) if isinstance(bad, str) else _reply(bad)

    _mock_llm(monkeypatch, handler)
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert not seg.used_llm and len(seg.questions) == 9
    assert any("大模型拆题失败" in w for w in seg.warnings)


async def test_llm_minor_errors_repaired(units, monkeypatch):
    rule = rule_segment(units, with_answer=True)
    groups = [{"no": i + 1, "type": q.type, "score": q.score, "units": list(q.unit_ids), "answer_units": []}
              for i, q in enumerate(rule.questions)]
    groups[2]["units"] += groups[1]["units"][:1]      # 第 3 题重复使用了第 2 题的单元
    groups[4]["units"].append("不存在的单元")            # 第 5 题含未知单元
    groups[6], groups[7] = groups[7], groups[6]        # 第 7、8 题顺序颠倒
    _mock_llm(monkeypatch, lambda _: _reply({"questions": groups}))
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert seg.used_llm and len(seg.questions) == 9
    assert [q.printed_no for q in seg.questions] == [q.printed_no for q in rule.questions]
    flagged = [i for i, q in enumerate(seg.questions) if "大模型分组有误，已自动修正" in q.flags]
    assert flagged == [2, 4] and all(seg.questions[i].confidence < 0.8 for i in flagged)
    assert any("3 处分组问题" in w for w in seg.warnings)


async def test_llm_reused_unit_rejected(units, monkeypatch):
    rule = rule_segment(units, with_answer=True)
    dup = rule.questions[0].unit_ids[0]
    groups = [{"units": [dup]}, {"units": [dup]}]
    _mock_llm(monkeypatch, lambda _: _reply({"questions": groups}))
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert not seg.used_llm


async def test_llm_non_streaming_response_supported(monkeypatch):
    payload = {"choices": [{"message": {"content": '```json\n{"ok": 1}\n```'}}]}
    _mock_llm(monkeypatch, lambda _: httpx.Response(200, json=payload))
    assert await llm.chat_json("s", "u", _llm_settings()) == {"ok": 1}


async def test_llm_disconnect_retried_then_reported(monkeypatch):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=req)

    _mock_llm(monkeypatch, handler)
    with pytest.raises(llm.LLMError, match="RemoteProtocolError"):
        await llm.chat_json("s", "u", _llm_settings())
    assert len(calls) == 2


async def test_llm_extra_body_is_sent(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return _reply({"ok": 1})

    _mock_llm(monkeypatch, handler)
    s = Settings(llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="m", llm_extra_body={"enable_thinking": False})
    await llm.chat_json("s", "u", s)
    assert seen["enable_thinking"] is False


@pytest.mark.parametrize("lines", [
    # 选项分行：拼接时应在选项前换行
    ["2．下列变化中，属于物理变化的是", "A．钢铁生锈", "B．瓷碗破碎", "C．食物腐败", "D．火药爆炸"],
    # 选项紧贴汉字（部分 PDF 文字层没有空格）
    ["2．下列变化中，属于物理变化的是A．钢铁生锈B．瓷碗破碎C．食物腐败D．火药爆炸"],
])
def test_options_split_without_spaces(lines):
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate(["1．第一题的题干内容比较长（ ）", "A．1", "B．2", "C．3", "D．4", *lines])]
    q = rule_segment(us, with_answer=False).questions[1]
    assert q.stem == "下列变化中，属于物理变化的是"
    assert q.options == ["钢铁生锈", "瓷碗破碎", "食物腐败", "火药爆炸"]
    assert q.type == "单选题" and q.confidence >= 0.8


def test_image_placeholders_removed_and_experiment_section():
    us = [Unit(f"u{i}", i, 1, t[0], t[1]) for i, t in enumerate([
        ("text", "三、实验题"), ("text", "16．某同学设计了如图所示的实验装置"), ("image", "[图]"),
        ("text", "(1)仪器 a 的名称是______；"),
    ])]
    q = rule_segment(us, with_answer=False).questions[0]
    assert q.type == "解答题" and "[图]" not in q.stem
    assert q.stem == "某同学设计了如图所示的实验装置\n(1)仪器 a 的名称是______；"


async def test_llm_group_with_label_before_number(monkeypatch):
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate([
        "一、单选题", "并列关系：", "3．化学概念间在逻辑上有如图所示的部分关系（ ）", "A．甲", "B．乙", "C．丙", "D．丁",
    ])]
    groups = [{"type": "单选题", "score": 0, "units": ["u1", "u2", "u3", "u4", "u5", "u6"]}]
    _mock_llm(monkeypatch, lambda _: _reply({"questions": groups}))
    seg = await segment(us, _llm_settings(), with_answer=False)
    q = seg.questions[0]
    assert q.printed_no == 3 and q.stem.startswith("化学概念间") and q.options == ["甲", "乙", "丙", "丁"]


def test_notice_items_are_not_questions():
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate([
        "注意事项：", "1．答题前，考生务必将自己的姓名填写在答题卡上。", "2．回答选择题时，选出每小题答案后，用铅笔把答案涂黑。",
        "一、单选题", "1．下列变化中，属于物理变化的是（ ）", "A．钢铁生锈", "B．瓷碗破碎", "C．食物腐败", "D．火药爆炸",
    ])]
    seg = rule_segment(us, with_answer=False)
    assert len(seg.questions) == 1 and seg.questions[0].stem.startswith("下列变化")
    assert "注意事项：" in [u.text for u in seg.preamble]


def test_numbered_answers_distributed():
    from app.pipeline.segment import Question, distribute_numbered_answers
    qs = [Question(n, "单选题", 5, f"第{n}题", [], None, None, [], 0.9) for n in (1, 2, 3, 4)]
    qs[0].answer = "C\n【2 题答案】\nA\n【3 题答案】\nD"
    qs[0].analysis = "第 1 题解析"
    qs[3].answer = "B"
    distribute_numbered_answers(qs)
    assert [(q.answer, q.analysis) for q in qs] == [("C", "第 1 题解析"), ("A", None), ("D", None), ("B", None)]
    # 对应的题已有答案、或题号不存在时，留在原题
    qs = [Question(1, "单选题", 5, "", [], "C【2 题答案】A【9 题答案】B", None, [], 0.9),
          Question(2, "单选题", 5, "", [], "D", None, [], 0.9)]
    distribute_numbered_answers(qs)
    assert qs[1].answer == "D" and "A" in qs[0].answer and "B" in qs[0].answer


def test_answer_from_analysis_and_table_keys():
    from app.pipeline.segment import Question, Unit, answer_from_analysis, finalize_answers
    assert answer_from_analysis("【解答】解：……故选：B.【点评】中档题") == "B"
    assert answer_from_analysis("所以 BC 正确；故选：BC.") == "BC"
    assert answer_from_analysis("可得离心率为 $\\sqrt{2}$ ，故答案为：$\\sqrt{2}$.【点评】") == "$\\sqrt{2}$"
    assert answer_from_analysis("没有结论") is None
    table = Unit("t1", 90, 7, "table", "[表] 题号 1 2 3 4 选项 A C D B 题号 9 10 答案 AC BD")
    qs = [Question(n, "单选题", 5, "", [], None, None, [], 0.9) for n in (1, 2, 3, 9, 11)]
    qs[1].answer = "D"  # 已有答案不覆盖
    qs[4].analysis = "【解答】……故选：A"
    finalize_answers(qs, [table])
    assert [q.answer for q in qs] == ["A", "D", "D", "AC", "A"]


async def test_classify_uses_file_name(monkeypatch):
    from app.pipeline.classify import classify
    # 合订本拆出的分册没有封面：正文只有学科标题，学段、类型、地区来自文件名
    meta = rule_classify("道德与法治试题\n一、选择题", "2026福建中考_道德与法治试题及参考答案")
    assert (meta.title, meta.stage, meta.subject, meta.paper_type) == ("道德与法治试题", "初中", "道德与法治", "中考真题")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["user"] = json.loads(req.content)["messages"][1]["content"]
        # 学段与学科矛盾：保留学科，学段留空后由规则结果补上
        return _reply({"title": "道德与法治试题", "stage": "高中", "subject": "道德与法治", "region": "福建"})

    _mock_llm(monkeypatch, handler)
    meta, _ = await classify("道德与法治试题", _llm_settings(), file_name="2026福建中考_道德与法治试题及参考答案.pdf")
    assert seen["user"].startswith("文件名：2026福建中考_道德与法治试题及参考答案\n")
    assert (meta.stage, meta.subject, meta.paper_type, meta.region) == ("初中", "道德与法治", "中考真题", "福建")


def test_numbered_answer_block_fills_empty_answers():
    from app.pipeline.segment import Question, Unit, finalize_answers
    block = Unit("a1", 60, 5, "text", "【1 题答案】\n【答案】C\n【2 题答案】\n【答案】A\n【3 题答案】\n【答案】D")
    qs = [Question(n, "单选题", 5, "", [], None, None, [], 0.9) for n in (1, 2, 3)]
    qs[2].answer = "B"  # 已关联的答案不覆盖
    finalize_answers(qs, [block])
    assert [q.answer for q in qs] == ["C", "A", "B"]


def test_region_skips_instruction_text():
    text = "2025年普通高等学校招生全国统一考试\n注意事项：考生务必将自己所在的市（县、区）、学校填写在答题卡上。"
    assert rule_classify(text).region == ""
    assert rule_classify("2026年福建省厦门市初三二检 化学").region == "福建 · 厦门"


async def test_inline_answers_do_not_lower_confidence(monkeypatch):
    # 解析版：每题之后紧跟【答案】【解析】；大模型把它们放进 answer_units，与规则切分实为一致
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate([
        "一、单选题", "1．实数 2024 的相反数是（ ）", "A．2024 B．-2024 C．1 D．0", "【答案】B", "【解析】",
        "【详解】解：2024 的相反数是 -2024，", "故选：B．",
        "2．下列各数中最小的是（ ）", "A．0 B．1 C．-1 D．2", "【答案】C", "【详解】-1 最小，故选 C．",
    ])]
    groups = [{"type": "单选题", "score": 0, "units": ["u1", "u2"], "answer_units": ["u3", "u4", "u5", "u6"]},
              {"type": "单选题", "score": 0, "units": ["u7", "u8"], "answer_units": ["u9", "u10"]}]
    _mock_llm(monkeypatch, lambda _: _reply({"questions": groups}))
    seg = await segment(us, _llm_settings(), with_answer=True)
    assert [q.answer for q in seg.questions] == ["B", "C"]
    assert all(q.confidence >= 0.8 and "规则与大模型切分不一致" not in q.flags for q in seg.questions)


# ---------------- 分值：试卷标注优先，其余按大题总分 / 整卷满分推算 ----------------

def fujian_math_units(fill_header: str = "二、填空题（本大题有6小题，每小题4分，共24分）") -> list[Unit]:
    """福建中考数学结构：选择 10×4、填空 6×4、解答 9 题共 86 分（逐题不标分值）。"""
    lines = ["2024年厦门市初中毕业班模拟考试 数学", "（试卷满分：150分 考试时间：120分钟）",
             "一、选择题（本大题有10小题，每小题4分，共40分）"]
    lines += [x for i in range(1, 11) for x in (f"{i}．下列各数中，第{i}题最小的数是（ ）", "A．1 B．2 C．3 D．4")]
    lines += [fill_header] + [f"{i}．计算第{i}题的结果为 ____．" for i in range(11, 17)]
    lines += ["三、解答题（本大题有9小题，共86分）"] + [f"{i}．如图，求解第{i}题的值并说明理由．" for i in range(17, 26)]
    return [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate(lines)]


def test_fujian_section_total_uses_known_layout():
    seg = rule_segment(fujian_math_units(), with_answer=False)
    assert seg.paper_total == 150 and seg.sections[3].total == 86
    scores = [q.score for q in seg.questions]
    assert scores[:16] == [4.0] * 16
    assert scores[16:] == [8, 8, 8, 8, 8, 10, 10, 12, 14] and sum(scores) == 150
    assert {q.score_source for q in seg.questions[16:]} == {"section_total"}
    assert "分值为默认值" not in seg.questions[20].flags


def test_header_exceptions_and_printed_scores_win():
    us = fujian_math_units("二、填空题（本大题有6小题，共26分，其中11题6分，其余每小题4分）")
    us[-1] = Unit(us[-1].id, us[-1].seq, 1, "text", "25．（16分）如图，求解第25题的值并说明理由．")
    seg = rule_segment(us, with_answer=False)
    by_no = {q.printed_no: q for q in seg.questions}
    assert by_no[11].score == 6 and by_no[12].score == 4
    # 题号后标注的分值不改动；其余 8 题分剩下的 70 分（没有已知分布时尽量均分）
    assert by_no[25].score == 16 and by_no[25].score_source == "question"
    assert [by_no[n].score for n in range(17, 25)] == [8, 8, 9, 9, 9, 9, 9, 9]
    assert sum(q.score for q in seg.questions) == 152  # 40 + 26 + 86：以试卷标注为准


def test_per_blank_questions_take_the_remainder():
    seg = rule_segment(fujian_math_units("二、填空题（第11题每空2分，其它每题4分，共28分）"), with_answer=False)
    by_no = {q.printed_no: q for q in seg.questions}
    assert by_no[11].score == 8 and by_no[11].score_source == "section_total"
    assert sum(q.score for q in seg.questions) == 154


def test_paper_total_only_lowers_unmarked_scores():
    lines = ["2024年期中考试 物理（满分：100分）", "一、选择题"]
    lines += [f"{i}．下列说法正确的是第{i}项（ ）\nA．甲 B．乙 C．丙 D．丁" for i in range(1, 21)]
    us = [Unit(f"u{i}", i, 1, "text", t) for i, t in enumerate(lines)]
    seg = rule_segment(us, with_answer=False)
    # 20 题 × 默认 5 分 = 100，与满分一致：不调整
    assert [q.score_source for q in seg.questions] == ["default"] * 20 and sum(q.score for q in seg.questions) == 100
    us += [Unit("x1", 99, 1, "text", "二、计算题"), Unit("x2", 100, 1, "text", "21．（10分）求物体的速度．")]
    seg = rule_segment(us, with_answer=False)
    # 超出满分：标注的 10 分保留，默认分值按比例下调到合计 100
    assert seg.questions[-1].score == 10 and sum(q.score for q in seg.questions) == 100
    assert {q.score_source for q in seg.questions[:20]} == {"paper_total"}
    # 少于满分（题目缺失）时不放大
    seg = rule_segment(us[:12], with_answer=False)
    assert sum(q.score for q in seg.questions) == 50


async def test_llm_scores_follow_paper_sections(monkeypatch):
    us = fujian_math_units()
    rule = rule_segment(us, with_answer=False)
    # 大模型把选择题都给成 5 分、解答题都给成 12 分（试卷没有逐题标注）
    groups = [{"no": q.printed_no, "type": q.type, "score": 5 if q.type == "单选题" else 12,
               "units": q.unit_ids, "answer_units": []} for q in rule.questions]
    _mock_llm(monkeypatch, lambda req: _reply({"questions": groups}))
    seg = await segment(us, _llm_settings(), with_answer=False)
    assert seg.used_llm
    scores = [q.score for q in seg.questions]
    assert scores[:10] == [4.0] * 10  # 「每小题 4 分」优先于大模型
    assert scores[16:] == [8, 8, 8, 8, 8, 10, 10, 12, 14] and sum(scores) == 150

    # 大模型分值与大题总分一致时保留（如从题目中读到了分值）
    llm_scores = [6, 8, 8, 9, 9, 10, 11, 12, 13]
    for g, s in zip(groups[16:], llm_scores):
        g["score"] = s
    seg = await segment(us, _llm_settings(), with_answer=False)
    assert [q.score for q in seg.questions[16:]] == llm_scores


@pytest.mark.parametrize("header, each, total, count, special, per_blank", [
    ("三、解答题（本大题有9小题，共86分）", None, 86, 9, {}, set()),
    ("三、解答题（86分）", None, 86, None, {}, set()),
    ("一、选择题（本大题10小题，每小题4分，共40分）", 4, 40, 10, {}, set()),
    ("二、填空题（本大题有6小题，共26分，其中11题6分，其余每小题4分）", 4, 26, 6, {11: 6}, set()),
    ("二、填空题（第11题每空2分，其它每题4分，共28分）", 4, 28, None, {}, {11}),
    ("二．填空题（共6小题，11、13小题每空2分，其余小题，每题3分，满分28分）", 3, 28, 6, {}, {11, 13}),
    ("二、填空题：本题共6小题，第11小题每空2分，共8分；第13小题第一空4分，第二空2分，共6分；其余每小题4分，共30分．",
     4, 30, 6, {11: 8, 13: 6}, set()),
    ("二、填空题（本大题共6小题，其中第11题每空1分，第12-16题每小题6分，共26分）",
     None, 26, 6, {n: 6 for n in range(12, 17)}, {11}),
    ("三、解答题（共9题，86分；其中17-21题每题8分，22-23题每题10分，第24题12分，第25题14分）",
     None, None, 9, {17: 8, 18: 8, 19: 8, 20: 8, 21: 8, 22: 10, 23: 10, 24: 12, 25: 14}, set()),
    ("三、计算题（共30分，19题6分，20题6分，21题6分，22题12分）", None, 30, None, {19: 6, 20: 6, 21: 6, 22: 12}, set()),
    ("一、选择题（56分，共16题。1-12为单选题，每题3分；13-16为多选题，每题5分，漏选得3分，错选得0分）",
     None, None, 16, {**{n: 3 for n in range(1, 13)}, **{n: 5 for n in range(13, 17)}}, set()),
    ("一、听力（2023-2024学年，每小题1.5分，共30分）", 1.5, 30, None, {}, set()),
])
def test_section_header_scores(header, each, total, count, special, per_blank):
    from app.pipeline.segment import _section_info
    info = _section_info(header)
    assert (info.each, info.total, info.count, info.special, info.per_blank) == (each, total, count, special, per_blank)


def test_rescore_existing_drafts():
    """已入库的旧结果（解答题默认 12 分）按试卷大题总分重算；试卷标注的分值保留。"""
    from types import SimpleNamespace
    from app.rescore import rescore
    us = fujian_math_units()
    blocks = [SimpleNamespace(id=u.id, seq=u.seq, page=1, bbox=[0, 0, 1, 1], type="text", content=u.text) for u in us]
    rule = rule_segment(us, with_answer=False)
    drafts = [SimpleNamespace(type=q.type, score=12.0 if q.type == "解答题" else 4.0, block_ids=q.unit_ids)
              for q in rule.questions]
    scores = rescore(blocks, drafts)
    assert scores[:16] == [4.0] * 16 and scores[16:] == [8, 8, 8, 8, 8, 10, 10, 12, 14]


def test_rescore_is_stable():
    """重算结果再重算不变：与大题总分一致的分值不被整卷满分（与大题合计不符时）再次下调。"""
    from types import SimpleNamespace
    from app.rescore import rescore
    us = fujian_math_units()
    us[1] = Unit(us[1].id, us[1].seq, 1, "text", "（试卷满分：130分 考试时间：120分钟）")  # 与大题合计 150 不符
    blocks = [SimpleNamespace(id=u.id, seq=u.seq, page=1, bbox=[0, 0, 1, 1], type="text", content=u.text) for u in us]
    rule = rule_segment(us, with_answer=False)
    drafts = [SimpleNamespace(type=q.type, score=12.0 if q.type == "解答题" else 4.0, block_ids=q.unit_ids)
              for q in rule.questions]
    first = rescore(blocks, drafts)
    for d, score in zip(drafts, first):
        d.score = score
    assert rescore(blocks, drafts) == first and first[16:] == [8, 8, 8, 8, 8, 10, 10, 12, 14]
