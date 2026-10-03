import json
import time

import httpx
import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.db import AiUsage, BankQuestion, DraftQuestion, SessionLocal, add_missing_columns
from app.pipeline import llm
from app.pipeline.answer import _normalize

from .test_api import client, parsed, upload, wait_done  # noqa: F401  复用已解析的试卷


def q(type_: str, options=None, images=None) -> DraftQuestion:  # noqa: ANN001
    return DraftQuestion(type=type_, stem="题干", options=options or [], images=images or [])


def test_normalize_choice_answers():
    assert _normalize(q("单选题", ["1", "2", "3", "4"]), {"answer": "答案是 b"})[0] == "B"
    assert _normalize(q("多选题", ["1", "2", "3", "4"]), {"answer": "D、A、C"})[0] == "ACD"
    ans, _, note = _normalize(q("单选题", ["1", "2", "3", "4"]), {"answer": "AB"})
    assert ans == "AB" and "不符合题型" in note
    assert "不符合题型" in _normalize(q("单选题", ["1", "2"]), {"answer": "D"})[2]
    with pytest.raises(ValueError):
        _normalize(q("填空题"), {"answer": " "})


def test_normalize_notes():
    assert _normalize(q("解答题", images=["a.png"]), {"answer": "x=1"})[2] == "题目含图，AI 未看到图片，答案可能不准确"
    assert _normalize(q("解答题"), {"answer": "x=1", "uncertain": True, "reason": "缺少表格数据"})[2] == "缺少表格数据"
    assert _normalize(q("填空题"), {"answer": "3；4", "analysis": "略"}) == ("3；4", "略", None)


def test_migration_adds_new_nullable_columns(scratch_engine):
    eng = scratch_engine
    with eng.begin() as c:  # 模拟升级前的旧表结构
        c.execute(text("CREATE TABLE draft_question (id VARCHAR(48) PRIMARY KEY, answer TEXT)"))
        c.execute(text("CREATE TABLE bank_question (id VARCHAR(48) PRIMARY KEY, answer TEXT)"))
    added = add_missing_columns(eng)
    assert "draft_question.answer_source" in added and "draft_question.answer_note" in added
    assert "bank_question.answer_note" in added
    assert add_missing_columns(eng) == []  # 幂等


@pytest.fixture
def llm_on(monkeypatch):
    s = get_settings()
    for k, v in {"llm_base_url": "https://llm.test/v1", "llm_api_key": "k", "llm_model": "qwen-test"}.items():
        monkeypatch.setattr(s, k, v)
    prompts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        user = body["messages"][1]["content"]
        prompts.append(user)
        ans = {"answer": "C", "analysis": "由题意可得。"} if "选项" in user else {"answer": "（1）略；（2）m ≤ -2"}
        content = json.dumps(ans, ensure_ascii=False)
        sse = f'data: {json.dumps({"choices": [{"delta": {"content": content}}]})}\n\n' \
              f'data: {json.dumps({"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 80}})}\n\n' \
              "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handler))
    return prompts


def wait_answers(client, job_id: str, timeout: float = 10) -> dict:  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/parse-jobs/{job_id}").json()["answerTask"]
        if t and t["status"] in ("done", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError("生成答案超时")


def test_generate_answers_flow(client, parsed, llm_on):  # noqa: F811
    job_id = parsed["id"]
    qs = client.get(f"/api/parse-jobs/{job_id}/questions").json()
    assert all(x["answerSource"] == "paper" for x in qs if x["answer"])

    # 原卷答案齐全：没有需要生成的题
    r = client.post(f"/api/parse-jobs/{job_id}/generate-answers", json={})
    assert r.status_code == 400 and "没有需要" in r.json()["message"]

    # 清空两道题的答案，模拟原卷缺答案
    for x in (qs[1], qs[7]):
        r = client.patch(f"/api/draft-questions/{x['id']}", json={"answer": None, "analysis": None})
        assert r.json()["answer"] is None and r.json()["answerSource"] is None

    r = client.post(f"/api/parse-jobs/{job_id}/generate-answers", json={})
    assert r.status_code == 202 and r.json()["total"] == 2
    assert r.json()["questionIds"] == [qs[1]["id"], qs[7]["id"]]
    task = wait_answers(client, job_id)
    assert (task["status"], task["done"], task["failed"]) == ("done", 2, 0)
    assert len(llm_on) == 2 and "选项：\nA．" in llm_on[0]

    after = {x["id"]: x for x in client.get(f"/api/parse-jobs/{job_id}/questions").json()}
    q2, q8 = after[qs[1]["id"]], after[qs[7]["id"]]
    assert (q2["answer"], q2["answerSource"], q2["analysis"]) == ("C", "ai", "由题意可得。")
    assert q8["answerSource"] == "ai" and q8["answer"].startswith("（1）")
    # 其他题的原卷答案不受影响
    assert after[qs[0]["id"]]["answer"] == "A" and after[qs[0]["id"]]["answerSource"] == "paper"

    # 老师修改后来源变为人工
    r = client.patch(f"/api/draft-questions/{q2['id']}", json={"answer": "B"})
    assert r.json()["answerSource"] == "manual" and r.json()["answerNote"] is None

    # 指定题目 + 覆盖：替换已有答案
    r = client.post(f"/api/parse-jobs/{job_id}/generate-answers", json={"questionIds": [q2["id"]], "overwrite": True})
    assert r.status_code == 202 and r.json()["total"] == 1
    wait_answers(client, job_id)
    assert client.get(f"/api/parse-jobs/{job_id}/questions").json()[1]["answerSource"] == "ai"

    # 用量按「answer」用途记到本任务
    with SessionLocal() as s:
        rows = list(s.scalars(select(AiUsage).where(AiUsage.job_id == job_id, AiUsage.purpose == "answer")))
    assert len(rows) == 3 and rows[0].prompt_tokens == 300


def wait_paper_answers(client, paper_id: str, timeout: float = 10) -> dict:  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/papers/{paper_id}/answer-task").json()
        if t and t["status"] in ("done", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError("生成答案超时")


def test_generate_answers_for_bank(client, parsed, llm_on):  # noqa: F811
    """入库后为缺答案的题补生成：写入题库与草稿题，撤销审核但保留归属，重新入库不会冲掉答案。"""
    job_id = parsed["id"]
    qs = client.get(f"/api/parse-jobs/{job_id}/questions").json()
    for x in (qs[1], qs[7]):
        client.patch(f"/api/draft-questions/{x['id']}", json={"answer": None, "analysis": None})
    ids = [x["id"] for x in qs]
    assert client.post(f"/api/parse-jobs/{job_id}/commit", json={"questionIds": ids, "force": True}).json()["savedCount"] == len(ids)

    bank = {b["source"]["no"]: b for b in client.get(f"/api/papers/{job_id}").json()["questions"]}
    b2, b8 = bank[qs[1]["no"]], bank[qs[7]["no"]]
    assert b2["answer"] is None and b8["answer"] is None
    # 空白答案仍属于缺答案，必须实际生成并同步，不能计为成功后原样跳过。
    with SessionLocal() as s:
        s.get(BankQuestion, b2["id"]).answer = " \n "
        s.get(DraftQuestion, qs[1]["id"]).answer = " \n "
        s.commit()
    r = client.post("/api/bank/review", json={"questionIds": [b2["id"]], "ownerId": "test-admin", "approved": True})
    assert r.status_code == 200

    # 指定已有答案的题：没有需要生成的
    r = client.post(f"/api/papers/{job_id}/generate-answers", json={"questionIds": [bank[qs[0]["no"]]["id"]]})
    assert r.status_code == 400 and "没有需要" in r.json()["message"]

    r = client.post(f"/api/papers/{job_id}/generate-answers", json={})
    assert r.status_code == 202 and r.json()["scope"] == "bank" and r.json()["questionIds"] == [b2["id"], b8["id"]]
    task = wait_paper_answers(client, job_id)
    assert (task["status"], task["done"], task["failed"]) == ("done", 2, 0)

    after = {b["id"]: b for b in client.get(f"/api/papers/{job_id}").json()["questions"]}
    assert (after[b2["id"]]["answer"], after[b2["id"]]["answerSource"]) == ("C", "ai")
    assert after[b8["id"]]["answer"].startswith("（1）")
    # 生成答案后需重新审核，归属不变；其他题不受影响
    assert after[b2["id"]]["reviewedAt"] is None and after[b2["id"]]["ownerId"] == "test-admin"
    assert after[bank[qs[0]["no"]]["id"]]["answerSource"] == "paper"

    # 草稿题同步写入，仍为已保存
    drafts = {x["id"]: x for x in client.get(f"/api/parse-jobs/{job_id}/questions").json()}
    assert (drafts[qs[1]["id"]]["answer"], drafts[qs[1]["id"]]["status"]) == ("C", "saved")
    client.post(f"/api/parse-jobs/{job_id}/commit", json={"questionIds": [qs[1]["id"]], "force": True})
    assert client.get(f"/api/papers/{job_id}").json()["questions"][1]["answer"] == "C"

    r = client.post(f"/api/papers/{job_id}/generate-answers", json={})
    assert r.status_code == 400 and "没有需要" in r.json()["message"]
    # 移出试卷库，避免后续用例入库同一份试卷时被判为重复
    assert client.delete(f"/api/papers/{job_id}").status_code == 204


def test_generate_answers_requires_llm(client, parsed):  # noqa: F811
    r = client.post(f"/api/parse-jobs/{parsed['id']}/generate-answers", json={"overwrite": True})
    assert r.status_code == 400 and "未配置大模型" in r.json()["message"]


def test_coef_semantics_migrated_once(scratch_engine):
    from app.db import Base, migrate_coef_semantics
    eng = scratch_engine
    Base.metadata.create_all(eng)
    from sqlalchemy.orm import Session
    from app.db import ParseJob
    with Session(eng) as s:
        s.add(ParseJob(id="j1", school_id="demo", file_name="t.pdf", file_count=1, file_size=1, file_type="pdf",
                       file_keys=[], options={}, status="done", progress=100, stages=[], warnings=[]))
        s.flush()
        s.add(DraftQuestion(id="q1", job_id="j1", no=1, type="单选题", score=5, page=1, stem="题干", options=[],
                            knowledge_points=[], coef=0.86, confidence=0.9, block_ids=[], regions=[], images=[]))
        s.commit()
    assert migrate_coef_semantics(eng) == 1
    assert migrate_coef_semantics(eng) == 0  # 只执行一次
    with eng.connect() as c:
        assert c.execute(text("SELECT coef FROM draft_question")).scalar() == pytest.approx(0.14)
