"""数据库模型。开发环境默认 SQLite，生产环境通过 DATABASE_URL 指向 PostgreSQL。"""

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
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
    knowledge_points: Mapped[list[Any]] = mapped_column(JSON, default=list)
    coef: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    block_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    regions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    images: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 题目内图片的存储 key
    duplicate_of: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="draft")


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


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as s:
        yield s
