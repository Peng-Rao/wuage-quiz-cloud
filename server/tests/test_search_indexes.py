import uuid

from sqlalchemy import select, text

from app import similar
from app.config import get_settings
from app.db import BankQuestion, DraftQuestion, ParseJob, SessionLocal, engine
from app.search_indexes import create_indexes, vector_index


def _question(qid, school, stem, vector=None, model="index-test", qtype="单选题"):
    return BankQuestion(id=qid, school_id=school, source_job_id=school, source_draft_id=qid,
                        type=qtype, score=5, coef=0.5, stem=stem, options=[],
                        embedding=vector, embedding_model=model if vector else None)


def test_hybrid_recall_is_bounded_scoped_and_indexes_are_reusable(monkeypatch):
    school = uuid.uuid4().hex[:16]
    target = "唯一目标：求集合A与B的交集"
    with SessionLocal() as session:
        session.add(_question(school + "-exact", school, target))  # 缺少向量仍须召回
        session.add(_question(school + "-semantic", school, "寻找两个集合共有的元素", [1, 0, 0]))
        session.add(_question(school + "-excluded", school, target, [1, 0, 0]))
        session.add(_question(school + "-foreign", "other-" + school, target, [1, 0, 0]))
        session.add(_question(school + "-type", school, target, [1, 0, 0], qtype="解答题"))
        session.add_all(_question(f"{school}-{i:03}", school, f"水的沸点与物态变化{i}", [0, 1, 0])
                        for i in range(80))
        session.commit()
    _, names = create_indexes()
    assert vector_index(BankQuestion, "index-test", 3).name in names
    assert create_indexes()[0] == 0
    monkeypatch.setattr(similar, "EXACT_SEARCH_LIMIT", 0)
    monkeypatch.setattr(get_settings(), "similar_candidates", 20)
    with SessionLocal() as session:
        rows = list(similar._candidates(session, school, qtype="单选题", exclude_job=None, scope="bank",
                                       with_vectors=True, vector=[1, 0, 0], model="index-test",
                                       query_text=target, exclude_ids={school + "-excluded"}, pool=20))
        assert len(rows) <= 40
        ids = {row.id for _, row in rows}
        assert {school + "-exact", school + "-semantic"} <= ids
        assert not {school + "-foreign", school + "-type", school + "-excluded"} & ids
        result = similar.search(session, school, target, vector=[1, 0, 0], model="index-test", qtype="单选题",
                                exclude_ids={school + "-excluded"}, min_score=0, limit=10)
        assert result[0].id == school + "-exact" and result[0].score == 1
        lexical = similar.search(session, school, target, min_score=0)
        assert lexical[0].score == 1
        # 非法/零向量退回文字查询，不能触发数据库维度或 NaN 错误。
        assert similar.search(session, school, target, vector=[0, 0, 0])[0].score == 1
        assert similar.search(session, school, target, vector=[float("nan")])[0].score == 1
        session.execute(text("SET LOCAL enable_seqscan=off"))
        stmt = (select(BankQuestion.id)
                .where(BankQuestion.embedding_model == "index-test")
                .where(text("vector_dims(embedding)=3"))
                .order_by(text("embedding::vector(3) <=> '[1,0,0]'::vector")).limit(20))
        plan = session.execute(text("EXPLAIN " + str(stmt.compile(engine, compile_kwargs={"literal_binds": True})))).all()
        assert any(vector_index(BankQuestion, "index-test", 3).name in row[0] for row in plan)


def test_text_edit_refreshes_recall_text_and_invalidates_old_embedding():
    qid, school = uuid.uuid4().hex, uuid.uuid4().hex
    with SessionLocal() as session:
        session.add(_question(qid, school, "旧的题干", [1, 0, 0]))
        session.commit()
        q = session.get(BankQuestion, qid)
        assert q.search_text == similar.normalize("旧的题干")
        q.stem, q.options = "新的题干", ["选项 A"]
        session.commit()
        assert q.search_text == similar.normalize("新的题干选项 A")
        assert q.embedding is None and q.embedding_model is None


def test_indexed_draft_recall_preserves_scope_filters(monkeypatch):
    school = uuid.uuid4().hex[:16]
    with SessionLocal() as session:
        for tag, status, kind, owner_school in (
            ("good", "done", None, school), ("excluded", "done", None, school),
            ("eval", "done", "eval", school), ("busy", "running", None, school),
            ("foreign", "done", None, "other-" + school), ("saved", "done", None, school),
        ):
            jid = school + "-" + tag
            session.add(ParseJob(id=jid, school_id=owner_school, file_name="范围.pdf", file_count=1,
                                 file_size=1, file_type="pdf", file_keys=[], options={}, status=status,
                                 kind=kind, progress=100, stages=[]))
            session.flush()
            session.add(DraftQuestion(id=jid, job_id=jid, no=1, type="单选题", score=5, page=1,
                                      stem="范围检索题目", options=[], coef=0.5, confidence=1))
        saved = _question(school + "-bank", school, "范围检索题目")
        saved.source_draft_id = school + "-saved"
        session.add(saved)
        session.commit()
    monkeypatch.setattr(similar, "EXACT_SEARCH_LIMIT", 0)
    with SessionLocal() as session:
        result = similar.search(session, school, "范围检索题目", scope="all", qtype="单选题",
                                exclude_job=school + "-excluded", exclude_ids={school + "-bank"})
        assert [(m.kind, m.id) for m in result] == [("draft", school + "-good")]


def test_long_unique_text_fits_gist_index():
    qid, school = uuid.uuid4().hex, uuid.uuid4().hex
    stem = "".join(chr(0x4e00 + i) for i in range(5000))
    with SessionLocal() as session:
        session.add(_question(qid, school, stem))
        session.commit()  # GiST 叶节点不能装下无限长的不同 trigram。
        q = session.get(BankQuestion, qid)
        assert len(q.search_text) == similar.SEARCH_TEXT_MAX_CHARS
        assert q.stem == stem
        assert similar.search(session, school, stem)[0].score == 1


def test_high_dimensional_vectors_use_binary_quantized_index(monkeypatch):
    school, dims, model = uuid.uuid4().hex[:16], 2048, "index-test-" + uuid.uuid4().hex[:6]
    near = [1.0] * dims
    far = [1.0 if i % 2 else -1.0 for i in range(dims)]
    with SessionLocal() as session:
        session.add(_question(school + "-near", school, "完全不同的文字甲", near, model=model))
        session.add_all(_question(f"{school}-{i:03}", school, f"无关题目{i}", far, model=model) for i in range(30))
        session.commit()
    _, names = create_indexes()
    index = vector_index(BankQuestion, model, dims)
    assert "_hnsw_bit2048_" in index.name and index.name in names
    monkeypatch.setattr(similar, "EXACT_SEARCH_LIMIT", 0)
    with SessionLocal() as session:
        result = similar.search(session, school, "查询文字", vector=near, model=model, qtype="单选题", min_score=0)
        assert result[0].id == school + "-near" and result[0].semantic == 1
        session.execute(text("SET LOCAL enable_seqscan=off"))
        order = similar.ann_order(BankQuestion.embedding, near)
        stmt = (select(BankQuestion.id).where(BankQuestion.embedding_model == model)
                .where(text(f"vector_dims(embedding)={dims}")).order_by(order).limit(20))
        plan = session.execute(text("EXPLAIN " + str(stmt.compile(engine, compile_kwargs={"literal_binds": True})))).all()
        assert any(index.name in row[0] for row in plan)
