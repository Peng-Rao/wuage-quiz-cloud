import json

import httpx
import pytest
from sqlalchemy import select

from app import similar
from app.config import Settings
from app.db import AiUsage, SessionLocal
from app.similar import calibrate, combine, embed, lexical, normalize

from .fixtures import make_exam_pdf
from .test_api import client, upload, wait_done  # noqa: F401

Q = "已知集合 A = {−1, 0, 1, 2}，B = { x | x² ≤ 1 }，则 A ∩ B = （ ）"


def test_normalize_and_lexical():
    assert normalize("A．$\\frac{1}{2}$ ＿＿＿ （ ）， x") == "a12x"
    assert lexical(Q, "已知集合A={−1,0,1,2}，B={x|x²≤1}，则A∩B=（　　）") == pytest.approx(1)
    same_template = lexical(Q, "已知集合 A = {−2, 0, 1, 3}，B = { x | x² ≤ 4 }，则 A ∩ B = （ ）")
    assert 0.5 < same_template < similar.DUPLICATE_SCORE
    assert lexical(Q, "下列变化中，属于物理变化的是（ ）") < 0.1
    assert lexical("", Q) == 0


def test_semantic_calibration():
    assert calibrate(0.5) == 0 and calibrate(0.99) == 1
    assert combine(0.4, None) == 0.4
    assert combine(0.4, 0.95) == pytest.approx(0.7)


async def test_embed_batches_and_records_usage(monkeypatch):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(len(body["input"]))
        data = [{"index": i, "embedding": [float(i + 1), 1.0]} for i in range(len(body["input"]))][::-1]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 7 * len(body["input"])}})

    monkeypatch.setattr(similar, "transport", httpx.MockTransport(handler))
    s = Settings(llm_base_url="https://llm.test/v1", llm_api_key="k", embedding_model="emb-test", embedding_batch=2)
    vecs = await embed(["a", "b", "c"], s)
    assert calls == [2, 1] and vecs == [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0]]  # 按 index 还原顺序
    with SessionLocal() as db:
        rows = list(db.scalars(select(AiUsage).where(AiUsage.model == "emb-test")))
    assert sum(r.prompt_tokens for r in rows) == 21 and {r.purpose for r in rows} == {"embed"}


async def test_embed_disabled_or_failing_returns_none(monkeypatch):
    assert await embed(["a"], Settings(embedding_model="")) is None
    monkeypatch.setattr(similar, "transport", httpx.MockTransport(lambda r: httpx.Response(500)))
    s = Settings(llm_base_url="https://llm.test/v1", llm_api_key="k", embedding_model="emb-test")
    assert await embed(["a"], s) is None


def _parse(client, name: str, dedupe: bool = True) -> dict:  # noqa: ANN001, F811
    key = upload(client, name, make_exam_pdf())
    job = client.post("/api/parse-jobs", json={"fileKeys": [key], "fileNames": [name],
                                               "options": {"dedupe": dedupe}}).json()
    return wait_done(client, job["id"])


def test_dedupe_against_bank_and_similar_queries(client):  # noqa: F811
    first = _parse(client, "相似题-原卷.pdf")
    qs = client.get(f"/api/parse-jobs/{first['id']}/questions").json()
    # 其他测试已将同一份试卷入库，这里强制保存
    r = client.post(f"/api/parse-jobs/{first['id']}/commit", json={"questionIds": [q["id"] for q in qs], "force": True})
    assert r.status_code == 200 and r.json()["savedCount"] == 9

    # 同一份试卷再上传一次：每道题都应命中校本题库
    second = _parse(client, "相似题-重复上传.pdf")
    dedupe = next(s for s in second["stages"] if s["stage"] == "dedupe")
    assert dedupe["status"] == "done" and dedupe["note"] == "9 道疑似重复"
    qs2 = client.get(f"/api/parse-jobs/{second['id']}/questions").json()
    assert all(q["duplicateOf"] for q in qs2)

    sim = client.get(f"/api/draft-questions/{qs2[0]['id']}/similar?scope=bank&limit=20").json()
    assert sim[0]["source"] == "bank" and sim[0]["duplicate"] and sim[0]["score"] == pytest.approx(1)
    assert sim[0]["semantic"] is None
    # 其他测试可能已将同一份试卷入库，满分结果中应包含本次入库的原卷
    assert "相似题-原卷.pdf" in {x["fileName"] for x in sim if x["score"] == pytest.approx(1)}
    assert all(x["type"] == "单选题" for x in sim)  # 只比较同题型

    r = client.post("/api/similar/search", json={"text": "已知集合 A={−1,0,1,2}，B={x|x²≤1}，求 A∩B", "limit": 3})
    assert r.status_code == 200 and r.json()[0]["stem"].startswith("已知集合")

    # 关闭查重：阶段跳过，不标记重复
    third = _parse(client, "相似题-不查重.pdf", dedupe=False)
    assert next(s for s in third["stages"] if s["stage"] == "dedupe")["status"] == "skipped"
    assert not any(q["duplicateOf"] for q in client.get(f"/api/parse-jobs/{third['id']}/questions").json())

    # scope=all 时也能找到其他试卷中尚未入库的草稿题
    qs3 = client.get(f"/api/parse-jobs/{third['id']}/questions").json()
    sim_all = client.get(f"/api/draft-questions/{qs3[0]['id']}/similar?scope=all&limit=10").json()
    assert {"bank", "draft"} <= {x["source"] for x in sim_all}
    assert all(x["jobId"] != third["id"] for x in sim_all)  # 不包含本卷
