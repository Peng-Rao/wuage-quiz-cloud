"""迁移向量字段并回填题库：python -m app.embedding_backfill [--school-id ID]。

迁移前备份数据库并暂停写入。每批独立提交，失败可重跑；同模型已有向量跳过。
"""
import argparse
import asyncio

from sqlalchemy import inspect, or_, select, text

from .config import get_settings
from .db import BankQuestion, SessionLocal, engine
from .similar import embed, embedding_enabled, question_text


def migrate(eng=engine):
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        inspector = inspect(conn)
        for table in ("bank_question", "draft_question", "knowledge_node"):
            if not inspector.has_table(table):
                continue
            column = next(c for c in inspector.get_columns(table) if c["name"] == "embedding")
            if str(column["type"]).upper() in {"JSON", "JSONB"}:
                conn.execute(text(
                    f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector "
                    "USING CASE WHEN embedding IS NULL OR embedding::text = 'null' "
                    "THEN NULL ELSE embedding::text::vector END"))


async def backfill(school_id=None, settings=None, *, after_id="", before_id=None):
    st = settings or get_settings()
    if not embedding_enabled(st):
        raise RuntimeError("请配置 EMBEDDING_PROVIDER 和 EMBEDDING_MODEL")
    count = 0
    last_id = after_id
    while True:
        with SessionLocal() as session:
            stmt = select(BankQuestion).where(
                BankQuestion.id > last_id,
                or_(BankQuestion.embedding.is_(None), BankQuestion.embedding_model.is_(None),
                    BankQuestion.embedding_model != st.embedding_model),
            ).order_by(BankQuestion.id).limit(st.embedding_batch)
            if school_id:
                stmt = stmt.where(BankQuestion.school_id == school_id)
            if before_id is not None:
                stmt = stmt.where(BankQuestion.id < before_id)
            rows = list(session.scalars(stmt))
            if not rows:
                break
            snapshots = [(q.id, q.stem, q.options) for q in rows]
        vectors = await embed([question_text(stem, options) for _, stem, options in snapshots], st)
        if vectors is None or len(vectors) != len(snapshots):
            raise RuntimeError("嵌入失败，本批未写入，可重新运行")
        with SessionLocal() as session:
            for (qid, stem, options), vector in zip(snapshots, vectors):
                q = session.get(BankQuestion, qid, with_for_update=True)
                # 推理期间编辑过的题目留给下次回填，避免写入过期向量。
                if q is not None and q.stem == stem and q.options == options:
                    q.embedding, q.embedding_model = vector, st.embedding_model
                    count += 1
            session.commit()
        last_id = snapshots[-1][0]
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--school-id")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--after-id", default="", help="仅回填大于该 ID 的题目")
    parser.add_argument("--before-id", help="仅回填小于该 ID 的题目")
    args = parser.parse_args()
    migrate()
    if not args.migrate_only:
        print(f"已更新 {asyncio.run(backfill(args.school_id, after_id=args.after_id, before_id=args.before_id))} 道题的向量")


if __name__ == "__main__":
    main()
