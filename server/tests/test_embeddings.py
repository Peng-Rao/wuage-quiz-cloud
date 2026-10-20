import pytest
from sqlalchemy import text

from app import similar
from app.config import Settings
from app.embedding_backfill import migrate
from app.embedding_backfill import backfill
from app.db import BankQuestion, SessionLocal


async def test_huggingface_runs_without_api_key(monkeypatch, tmp_path):
    calls = []

    class Model:
        def embed_documents(self, texts):
            calls.append(texts)
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(similar, "_local_embeddings", lambda *args: Model())
    settings = Settings(embedding_provider="huggingface", embedding_model="local-test",
                        embedding_cache_dir=str(tmp_path))
    assert similar.embedding_enabled(settings)
    assert await similar.embed(["中文题目", ""], settings) == [[1.0, 0.0], [1.0, 0.0]]
    assert calls == [["中文题目", " "]]


async def test_huggingface_failure_falls_back(monkeypatch):
    def fail(*args):
        raise OSError("model unavailable")
    monkeypatch.setattr(similar, "_local_embeddings", fail)
    assert await similar.embed(["题目"], Settings(embedding_provider="huggingface", embedding_model="x")) is None


def test_migration_preserves_vectors_and_json_null(scratch_engine):
    with scratch_engine.begin() as conn:
        conn.execute(text("CREATE TABLE bank_question (id text PRIMARY KEY, embedding json)"))
        conn.execute(text("INSERT INTO bank_question VALUES ('a', '[1,2,3]'), ('b', 'null'), ('c', NULL)"))
    migrate(scratch_engine)
    migrate(scratch_engine)
    with scratch_engine.connect() as conn:
        rows = conn.execute(text("SELECT id, embedding::text FROM bank_question ORDER BY id")).all()
        assert rows == [("a", "[1,2,3]"), ("b", None), ("c", None)]
        assert conn.execute(text("SELECT 1 - (embedding <=> '[1,2,3]'::vector) FROM bank_question WHERE id='a'")).scalar() == pytest.approx(1)


async def test_backfill_is_scoped_and_idempotent(monkeypatch):
    from app import embedding_backfill
    import uuid

    school = uuid.uuid4().hex
    qid = uuid.uuid4().hex
    with SessionLocal() as session:
        session.add(BankQuestion(id=qid, school_id=school, source_job_id=qid, source_draft_id=qid, type="单选题",
                                 score=5, coef=0.5, stem="求集合交集", options=[]))
        session.commit()

    async def fake_embed(texts, settings):
        return [[1.0, 2.0, 3.0] for _ in texts]
    monkeypatch.setattr(embedding_backfill, "embed", fake_embed)
    st = Settings(embedding_provider="huggingface", embedding_model="backfill-test")
    assert await backfill(school, st, after_id=qid) == 0
    assert await backfill(school, st, before_id=qid) == 0
    assert await backfill(school, st) == 1
    assert await backfill(school, st) == 0
    with SessionLocal() as session:
        row = session.get(BankQuestion, qid)
        assert row.embedding == [1.0, 2.0, 3.0]
        matches = similar.search(session, school, row.stem, vector=[1.0, 2.0, 3.0], model=st.embedding_model)
        assert matches[0].semantic == 1
        # 不同维度/模型不能计算距离或产生语义分数。
        assert similar.search(session, school, row.stem, vector=[1.0, 2.0], model=st.embedding_model)[0].semantic is None
        assert similar.search(session, school, row.stem, vector=[1.0, 2.0, 3.0], model="other")[0].semantic is None

    async def fail_embed(texts, settings):
        return None
    monkeypatch.setattr(embedding_backfill, "embed", fail_embed)
    with pytest.raises(RuntimeError, match="本批未写入"):
        await backfill(school, st.model_copy(update={"embedding_model": "replacement"}))
    with SessionLocal() as session:
        assert session.get(BankQuestion, qid).embedding_model == "backfill-test"


async def test_multimodal_embeds_one_text_per_request_and_retries(monkeypatch):
    import json

    import httpx
    from sqlalchemy import select

    from app.db import AiUsage
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path == "/v1/embeddings/multimodal"
        assert request.headers["authorization"] == "Bearer k"
        if len(calls) == 1:
            return httpx.Response(429)  # 限流后重试
        text = body["input"][0]["text"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [float(len(text)), 1]}],
                                         "usage": {"prompt_tokens": 3}})

    monkeypatch.setattr(similar, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(similar, "EMBED_RETRY_DELAY", 0)
    s = Settings(embedding_provider="multimodal", llm_base_url="https://hub.test/v1", llm_api_key="k",
                 embedding_model="mm-test", embedding_concurrency=1)
    assert similar.embedding_enabled(s)
    # 空白文本不请求接口（接口会返回 400），对应位置为 None
    assert await similar.embed(["一", "二三", " "], s) == [[1.0, 1.0], [2.0, 1.0], None]
    assert len(calls) == 3
    assert calls[-1] == {"model": "mm-test", "input": [{"type": "text", "text": "二三"}], "encoding_format": "float"}
    with SessionLocal() as db:
        rows = list(db.scalars(select(AiUsage).where(AiUsage.model == "mm-test")))
    assert sum(r.prompt_tokens for r in rows) == 6


async def test_multimodal_failure_falls_back(monkeypatch):
    import httpx
    monkeypatch.setattr(similar, "transport", httpx.MockTransport(lambda r: httpx.Response(400)))
    s = Settings(embedding_provider="multimodal", llm_base_url="https://hub.test/v1", llm_api_key="k",
                 embedding_model="mm-test")
    assert await similar.embed(["题目"], s) is None
