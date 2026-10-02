"""自动入库：解析完成后确定的题直接存入题库，不确定的留给人工审核。"""

import pytest
from sqlalchemy import select

from app.bank import review_reasons
from app.config import get_settings
from app.db import BankQuestion, DraftQuestion, ParseJob, SessionLocal

from .fixtures import make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401

EXAM = [
    ("厦门市 2026—2027 学年七年级上学期自动入库测试卷", 14),
    ("一、单选题：本题共 3 小题，每小题 5 分，共 15 分。", 10.5),
    ("1．已知 3x + 5 = 20，则 x 的值是（ ）", 10.5),
    ("A．3    B．4    C．5    D．6", 10.5),
    ("2．等腰三角形的顶角为 40°，则底角的度数为（ ）", 10.5),
    ("A．40°    B．60°    C．70°    D．80°", 10.5),
    ("3．下列运算正确的是（ ）", 10.5),
    ("A．a·a = 2a    B．2a + 3a = 5a    C．a + a = a    D．3a - a = 3", 10.5),
    ("二、填空题：本题共 1 小题，每小题 5 分，共 5 分。", 10.5),
    ("4．计算：(-2)^2 + |-3| = ________.", 10.5),
    None,
    ("参考答案", 13),
    ("1-3 CCB", 10.5),
]


def draft(**kw) -> DraftQuestion:  # noqa: ANN003
    base = dict(stem="题干", options=[], answer="A", analysis=None, answer_note=None, confidence=0.9, duplicate_of=None)
    return DraftQuestion(**{**base, **kw})


@pytest.mark.parametrize("kw,reasons", [
    ({}, []),
    ({"confidence": 0.7}, ["置信度低"]),
    ({"stem": " "}, ["题干为空"]),
    ({"options": ["A. [公式]"]}, ["公式未识别"]),
    ({"answer": ""}, ["缺少答案"]),
    ({"answer_note": "题目含图，AI 未看到图片"}, ["答案待确认"]),
    ({"duplicate_of": "k1"}, ["疑似重复"]),
    ({"confidence": 0.5, "answer": None}, ["置信度低", "缺少答案"]),
])
def test_review_reasons(kw, reasons):
    assert review_reasons(draft(**kw), 0.8, require_answer=True) == reasons


def test_answer_not_required_when_disabled():
    assert review_reasons(draft(answer=None), 0.8, require_answer=False) == []


def parse(client, name: str, data: bytes) -> dict:  # noqa: F811
    key = upload(client, name, data)
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": [name], "options": {}}).json()
    return wait_done(client, job["id"])


def test_auto_commit_after_parse(client, monkeypatch):  # noqa: F811
    s = get_settings()
    monkeypatch.setattr(s, "auto_commit", True)
    data = make_exam_pdf(EXAM)
    job = parse(client, "自动入库测试.pdf", data)
    assert job["status"] == "done", job.get("error")
    with SessionLocal() as db:
        drafts = list(db.scalars(select(DraftQuestion).where(DraftQuestion.job_id == job["id"]).order_by(DraftQuestion.no)))
        bank = list(db.scalars(select(BankQuestion).where(BankQuestion.source_job_id == job["id"])))
        warnings = db.get(ParseJob, job["id"]).warnings
    saved = [d for d in drafts if d.status == "saved"]
    pending = [d for d in drafts if d.status == "draft"]
    # 入库与否和判定规则一致：确定的全部入库，不确定的全部留给人工
    assert saved and all(not review_reasons(d, 0.8, True) for d in saved)
    assert all(review_reasons(d, 0.8, True) for d in pending)
    assert {b.source_draft_id for b in bank} == {d.id for d in saved}
    assert all(b.reviewed_at is None for b in bank)  # 分配给教师的审核不变
    # 第 4 题没有答案，留待人工审核
    q4 = next(d for d in drafts if "|-3|" in d.stem)
    assert q4.status == "draft" and "缺少答案" in review_reasons(q4, 0.8, True)
    assert job["savedCount"] == len(saved)
    assert any(w.startswith(f"自动入库 {len(saved)} 道") for w in warnings)

    # 同一份试卷再次解析：疑似重复 / 整卷已在试卷库中，不会重复入库
    again = parse(client, "自动入库测试（重复）.pdf", data)
    assert again["status"] == "done" and again["savedCount"] == 0


def test_auto_commit_disabled_keeps_drafts(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "auto_commit", False)
    # 换掉标题与题目，避免与上一个用例的试卷重复
    exam = [None if x is None else (x[0].replace("自动入库测试卷", "手动入库测试卷").replace("3x + 5 = 20", "4x + 2 = 18"), x[1])
            for x in EXAM]
    job = parse(client, "手动入库测试.pdf", make_exam_pdf(exam))
    assert job["status"] == "done" and job["savedCount"] == 0
