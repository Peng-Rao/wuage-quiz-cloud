"""端到端：上传 → 解析（轻量引擎 + 规则拆题）→ 核对编辑 → 入库。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .fixtures import EXPECTED, make_exam_pdf


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"username": "test-admin", "password": "test-password-123"}).status_code == 200
        yield c


def upload(client: TestClient, name: str, data: bytes) -> str:
    r = client.post("/api/uploads", json={"fileName": name, "fileSize": len(data), "contentType": "application/pdf"})
    assert r.status_code == 200, r.text
    ticket = r.json()
    assert client.put(ticket["uploadUrl"], content=data).status_code == 204
    return ticket["fileKey"]


def wait_done(client: TestClient, job_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/parse-jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("解析超时")


@pytest.fixture(scope="module")
def parsed(client):
    key = upload(client, "海淀期中.pdf", make_exam_pdf())
    r = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["海淀期中.pdf"],
                                             "options": {"ocr": True, "answer": True, "dedupe": True, "knowledge": True}})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued" and [s["stage"] for s in job["stages"]] == \
        ["ocr", "classify", "segment", "knowledge", "difficulty", "dedupe"]
    return wait_done(client, job["id"])


def test_job_completes_with_lite_parser(parsed):
    assert parsed["status"] == "done", parsed.get("error")
    assert parsed["parser"] == "lite"  # 未配置 MinerU Token 时降级
    assert parsed["pageCount"] == 3 and parsed["progress"] == 100
    stages = {s["stage"]: s for s in parsed["stages"]}
    assert stages["segment"]["note"] == "9 道题（规则）"
    assert stages["knowledge"]["status"] == "skipped"
    assert parsed["meta"]["stage"] == "高中" and parsed["meta"]["subject"] == "数学"
    assert parsed["questionCount"] == 9 and parsed["reviewCount"] == 0


def test_questions_and_source(client, parsed):
    qs = client.get(f"/api/parse-jobs/{parsed['id']}/questions").json()
    assert [(q["type"], q["score"], len(q["options"]), q["answer"]) for q in qs] == \
        [(t, s, n, a) for t, s, n, a in EXPECTED]
    assert [q["no"] for q in qs] == list(range(1, 10))
    q1 = qs[0]
    assert q1["regions"][0]["page"] == 1 and all(0 <= v <= 1 for v in q1["regions"][0]["bbox"])
    src = client.get(f"/api/draft-questions/{q1['id']}/source").json()
    assert src[0]["page"] == 1
    img = client.get(src[0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_sse_stream_emits_final_snapshot(client, parsed):
    with client.stream("GET", f"/api/parse-jobs/{parsed['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line.startswith("data: ")]
    assert json.loads(lines[-1][6:])["status"] == "done"


def test_edit_merge_split_commit(client, parsed):
    job_id = parsed["id"]
    qs = client.get(f"/api/parse-jobs/{job_id}/questions").json()

    r = client.patch(f"/api/draft-questions/{qs[3]['id']}", json={"answer": "D", "coef": 0.5})
    assert r.status_code == 200 and r.json()["confidence"] == 1.0 and r.json()["coef"] == 0.5
    assert client.patch(f"/api/draft-questions/{qs[3]['id']}", json={"type": "判断题"}).status_code == 422

    # 第一题不能与上一题合并
    r = client.post(f"/api/draft-questions/{qs[0]['id']}/merge-previous")
    assert r.status_code == 400 and "第一题" in r.json()["message"]

    # 拆分第 8 题的两个小问
    r = client.post(f"/api/draft-questions/{qs[7]['id']}/split")
    assert r.status_code == 200
    after = r.json()
    assert len(after) == 10 and [q["no"] for q in after] == list(range(1, 11))
    assert after[7]["stem"].endswith("求 A ∪ B；") and after[8]["stem"].startswith("已知集合")
    assert after[7]["score"] + after[8]["score"] == 12
    assert after[7]["answer"] == "（1）A ∪ B = (1, 5)；" and after[8]["answer"] == "（2）m ≤ -2"

    # 没有小问标记的题拆分失败
    assert client.post(f"/api/draft-questions/{after[0]['id']}/split").status_code == 400

    # 再把它们合并回去
    r = client.post(f"/api/draft-questions/{after[8]['id']}/merge-previous")
    merged = r.json()
    assert len(merged) == 9 and merged[7]["score"] == 12

    # 分类修改
    meta = dict(parsed["meta"], region="北京 · 西城")
    assert client.put(f"/api/parse-jobs/{job_id}/meta", json=meta).json()["region"] == "北京 · 西城"

    # 入库：重复提交不产生重复题
    ids = [q["id"] for q in merged[:5]]
    for _ in range(2):
        r = client.post(f"/api/parse-jobs/{job_id}/commit", json={"questionIds": ids}).json()
        assert r["savedCount"] == 5 and r["savedIds"] == ids and r["skipped"] == [] and r["duplicatePaper"] is None
    job = client.get(f"/api/parse-jobs/{job_id}").json()
    assert job["savedCount"] == 5 and job["meta"]["region"] == "北京 · 西城"
    recent = client.get("/api/parse-jobs?recent=1").json()
    assert recent[0]["jobId"] == job_id and recent[0]["savedCount"] == 5
    assert recent[0]["createdAt"].endswith(("Z", "+00:00"))


def test_material_edit_and_commit(client, parsed):
    """阅读材料可编辑；完形填空等题题干可以为空，但题干与材料不能都为空；入库后保留材料。"""
    job_id = parsed["id"]
    q = client.get(f"/api/parse-jobs/{job_id}/questions").json()[0]
    url = f"/api/draft-questions/{q['id']}"
    r = client.patch(url, json={"material": "Read the passage and answer.", "stem": ""})
    assert r.status_code == 200 and r.json()["material"] == "Read the passage and answer." and r.json()["stem"] == ""
    assert client.patch(url, json={"material": ""}).status_code == 422
    assert client.post(f"/api/parse-jobs/{job_id}/commit", json={"questionIds": [q["id"]]}).json()["savedCount"] == 1
    bank = client.get("/api/bank/questions", params={"paperId": job_id}).json()["items"]
    assert any(b["material"] == "Read the passage and answer." for b in bank)


def test_upload_validation(client):
    r = client.post("/api/uploads", json={"fileName": "a.exe", "fileSize": 10})
    assert r.status_code == 400 and "仅支持" in r.json()["message"]
    r = client.post("/api/uploads", json={"fileName": "a.pdf", "fileSize": 60 * 1024 * 1024})
    assert r.status_code == 400
    r = client.post("/api/parse-jobs", json={"fileKeys": ["uploads/none.pdf"], "fileNames": ["a.pdf"]})
    assert r.status_code == 400 and "尚未上传" in r.json()["message"]
    k1, k2 = upload(client, "a.pdf", b"%PDF-1.4"), upload(client, "b.png", b"PNG")
    r = client.post("/api/parse-jobs", json={"fileKeys": [k1, k2], "fileNames": ["a.pdf", "b.png"]})
    assert r.status_code == 400 and "多个文件仅支持图片" in r.json()["message"]
    # 路径穿越与越权读取
    assert client.put("/api/files/jobs/x.png", content=b"1").status_code == 403
    assert client.get("/api/files/" + k1).status_code == 403
    assert client.get("/api/files/jobs/../uploads/x").status_code in (403, 404)


def test_broken_pdf_fails_with_message(client):
    key = upload(client, "坏文件.pdf", b"%PDF-1.4 not really a pdf")
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": ["坏文件.pdf"]}).json()
    done = wait_done(client, job["id"])
    assert done["status"] == "failed" and done["error"]
    assert any(s["status"] == "failed" for s in done["stages"])


def test_usage_endpoints(client, parsed, monkeypatch):
    from app.config import get_settings
    from app.usage import record

    job_id = parsed["id"]
    # 不受本机 .env 中单价配置影响
    monkeypatch.setattr(get_settings(), "llm_prices", {})
    monkeypatch.setattr(get_settings(), "mineru_price_per_page", None)
    # 未启用大模型与 MinerU 时没有用量
    assert client.get(f"/api/parse-jobs/{job_id}").json()["usage"] is None
    record("llm", "classify", "qwen-test", prompt_tokens=800, completion_tokens=100, job_id=job_id)
    record("llm", "segment", "qwen-test", prompt_tokens=6000, completion_tokens=2000, reasoning_tokens=900, job_id=job_id)
    record("mineru", "parse", "mineru-vlm", pages=3, job_id=job_id)

    u = client.get(f"/api/parse-jobs/{job_id}/usage").json()
    assert [c["purpose"] for c in u["calls"]] == ["classify", "segment", "parse"]
    assert u["summary"]["totalTokens"] == 8900 and u["summary"]["pages"] == 3
    assert u["summary"]["cost"] is None and u["summary"]["priced"] is False

    # 补填单价后，历史用量按新单价重算
    monkeypatch.setattr(get_settings(), "llm_prices", {"qwen-test": {"input": 2, "output": 8}})
    monkeypatch.setattr(get_settings(), "mineru_price_per_page", 0.1)
    u = client.get(f"/api/parse-jobs/{job_id}/usage").json()
    expected = (6800 * 2 + 2100 * 8) / 1e6 + 0.3
    assert u["summary"]["priced"] is True and u["summary"]["cost"] == pytest.approx(expected)
    assert u["calls"][2]["cost"] == pytest.approx(0.3)
    assert client.get(f"/api/parse-jobs/{job_id}").json()["usage"]["cost"] == pytest.approx(expected)

    ov = client.get("/api/usage/summary?days=30").json()
    assert ov["jobs"] >= 1 and ov["summary"]["pages"] >= 3
    assert ov["costPerJob"] == pytest.approx(ov["summary"]["cost"] / ov["jobs"])
    assert ov["daily"] and ov["daily"][-1]["cost"] is not None
    assert client.get("/api/usage/summary?days=0").status_code == 422
