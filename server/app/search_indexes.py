"""回填检索文本并在线建索引：python -m app.search_indexes。

索引在事务外并发创建；可重复执行。新嵌入模型完成回填后也运行此命令。
"""
import hashlib

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session, load_only

from .db import BankQuestion, DraftQuestion, ParseJob, engine
from .similar import HNSW_MAX_DIMS, SEARCH_TEXT_MAX_CHARS, binary_code, normalize, question_text


def vector_index(cls, model, dimensions):
    key = hashlib.sha256(model.encode()).hexdigest()[:12]
    binary = dimensions > HNSW_MAX_DIMS
    name = f"ix_{cls.__tablename__}_hnsw_{'bit' if binary else ''}{dimensions}_{key}"
    # 临时 Table 防止每次运行将同名动态索引重复注册到 ORM 元数据。
    from sqlalchemy import Column, MetaData, String, Table
    table = Table(cls.__tablename__, MetaData(), Column("embedding", Vector()),
                  Column("embedding_model", String(64)))
    # 超过 2000 维时索引二值量化后的向量（汉明距离），召回后仍按原向量余弦精排
    expression = (binary_code(table.c.embedding, dimensions) if binary
                  else cast(table.c.embedding, Vector(dimensions))).label("embedding")
    return Index(name, expression, postgresql_using="hnsw",
                 postgresql_ops={"embedding": "bit_hamming_ops" if binary else "vector_cosine_ops"},
                 postgresql_where=and_(table.c.embedding_model == model,
                                       func.vector_dims(table.c.embedding) == dimensions),
                 postgresql_concurrently=True)


def prepare_text(eng=engine):
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        for cls in (BankQuestion, DraftQuestion):
            conn.execute(text(f"ALTER TABLE {cls.__tablename__} ADD COLUMN IF NOT EXISTS search_text text"))
    total = 0
    for cls in (BankQuestion, DraftQuestion):
        while True:
            with Session(eng) as session:
                rows = list(session.scalars(select(cls).where(or_(cls.search_text.is_(None),
                                            func.length(cls.search_text) > SEARCH_TEXT_MAX_CHARS)).order_by(cls.id)
                                            .options(load_only(cls.id, cls.stem, cls.options, cls.search_text))
                                            .limit(500).with_for_update()))
                if not rows:
                    break
                for row in rows:
                    row.search_text = normalize(question_text(row.stem, row.options))[:SEARCH_TEXT_MAX_CHARS]
                session.commit()
                total += len(rows)
    return total


def create_indexes(eng=engine):
    count = prepare_text(eng)
    created = []
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        def ensure(index):
            valid = conn.execute(text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=:name"), {"name": index.name}).scalar()
            if valid is False:
                index.drop(conn, checkfirst=True)
            index.create(conn, checkfirst=True)
            created.append(index.name)

        for cls in (BankQuestion, DraftQuestion, ParseJob):
            for index in sorted(cls.__table__.indexes, key=lambda item: item.name):
                if index.name.endswith(("_search_text_trgm", "_missing_search_text", "_school_type_id",
                                        "_job_type_id", "_school_status_kind_id")):
                    # 使用克隆，避免修改应用共享的索引元数据。
                    from sqlalchemy import MetaData
                    table = cls.__table__.to_metadata(MetaData())
                    clone = next(i for i in table.indexes if i.name == index.name)
                    clone.dialect_options["postgresql"]["concurrently"] = True
                    ensure(clone)
        groups = set()
        for cls in (BankQuestion, DraftQuestion):
            groups.update(conn.execute(select(cls.embedding_model, func.vector_dims(cls.embedding).label("dimensions"))
                                       .where(cls.embedding.is_not(None), cls.embedding_model.is_not(None))
                                       .distinct()).all())
        for cls in (BankQuestion, DraftQuestion):
            # 空草稿表也为题库中已有的模型预建索引，后续解析完成即可使用。
            for model, dimensions in sorted(groups):
                if not 1 <= dimensions <= 64000:
                    continue  # pgvector HNSW bit 索引最多支持 64000 维。
                index = vector_index(cls, model, dimensions)
                ensure(index)
            conn.execute(text(f"ANALYZE {cls.__tablename__}"))
    return count, created


if __name__ == "__main__":
    count, indexes = create_indexes()
    print(f"已回填 {count} 条检索文本；索引已就绪：{', '.join(indexes)}")
