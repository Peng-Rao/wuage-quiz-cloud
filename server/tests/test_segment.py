import json

import httpx
import pytest

from app.config import Settings
from app.pipeline import llm
from app.pipeline.classify import rule_classify
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


@pytest.mark.parametrize("bad", [
    {"questions": [{"units": ["不存在的单元"]}]},
    {"questions": []},
    "不是 JSON",
])
async def test_llm_invalid_falls_back_to_rules(units, monkeypatch, bad):
    def handler(_: httpx.Request) -> httpx.Response:
        return _sse(bad) if isinstance(bad, str) else _reply(bad)

    _mock_llm(monkeypatch, handler)
    seg = await segment(units, _llm_settings(), with_answer=True)
    assert not seg.used_llm and len(seg.questions) == 9
    assert any("大模型拆题失败" in w for w in seg.warnings)


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
