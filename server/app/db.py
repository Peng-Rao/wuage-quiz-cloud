"""数据库模型。开发环境默认 SQLite，生产环境通过 DATABASE_URL 指向 PostgreSQL。"""

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class ParseJob(Base):
    __tablename__ = "parse_job"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_count: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int] = mapped_column(Integer)
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / docx / image
    file_keys: Mapped[list[Any]] = mapped_column(JSON)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON)
    parser: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stages: Mapped[list[Any]] = mapped_column(JSON)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 解析过程中的告警（如降级、跳过大模型），供排查
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 最近一次「AI 生成答案」任务：{status, total, done, failed, questionIds, overwrite, error}
    answer_task: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ParseBlock(Base):
    """解析引擎输出的中间表示（IR），拆题、合并、拆分、原图回溯都基于它。"""

    __tablename__ = "parse_block"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("parse_job.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 阅读顺序
    page: Mapped[int] = mapped_column(Integer)  # 从 1 开始
    bbox: Mapped[list[Any]] = mapped_column(JSON)  # 归一化 [x0, y0, x1, y1]
    type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    image_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)


class DraftQuestion(Base):
    __tablename__ = "draft_question"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("parse_job.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    page: Mapped[int] = mapped_column(Integer)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 答案来源：paper 原卷识别 / ai 大模型生成 / manual 人工修改
    answer_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # AI 生成答案的提示，如「题目含图，AI 未看到图片，答案可能不准确」
    answer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_points: Mapped[list[Any]] = mapped_column(JSON, default=list)
    coef: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    block_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    regions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    images: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 题目内图片的存储 key
    duplicate_of: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="draft")


class AiUsage(Base):
    """每一次外部 AI 调用（大模型、MinerU）的用量。费用在查询时按当前单价计算。"""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # llm / mineru
    purpose: Mapped[str] = mapped_column(String(16))   # classify / segment / parse
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 已包含在 completion_tokens 中
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)     # 已包含在 prompt_tokens 中
    pages: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # 服务端未返回用量时按字符数估算
    estimated: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(8), default="ok")  # ok / error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BankQuestion(Base):
    """校本题库。P1 仅保存入库快照，检索、审核在后续阶段实现。"""

    __tablename__ = "bank_question"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    source_job_id: Mapped[str] = mapped_column(String(32), index=True)
    source_draft_id: Mapped[str] = mapped_column(String(48), unique=True)
    type: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    knowledge_points: Mapped[list[Any]] = mapped_column(JSON, default=list)
    coef: Mapped[float] = mapped_column(Float)
    images: Mapped[list[Any]] = mapped_column(JSON, default=list)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    _settings.db_url,
    connect_args={"check_same_thread": False} if _settings.db_url.startswith("sqlite") else {},
)

if _settings.db_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):  # noqa: ANN001
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def add_missing_columns(eng=None) -> list[str]:  # noqa: ANN001
    """轻量迁移：给已有表补上新增的可空列（create_all 不会修改已存在的表），返回新增的列。
    只处理加列；改列、删列等变更上线前需换成 Alembic。"""
    eng = eng or engine
    insp = inspect(eng)
    added: list[str] = []
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(eng.dialect)}"))
                    added.append(f"{table.name}.{col.name}")
    return added


def init_db() -> None:
    Base.metadata.create_all(engine)
    add_missing_columns()


def get_session() -> Iterator[Session]:
    with SessionLocal() as s:
        yield s
