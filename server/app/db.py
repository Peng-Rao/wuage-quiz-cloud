"""数据库模型（PostgreSQL，地址见 DATABASE_URL）。"""

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from fastapi import Request

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    subjects: Mapped[list[Any]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)


class LoginSession(Base):
    __tablename__ = "login_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    __tablename__ = "login_throttle"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UploadOwner(Base):
    __tablename__ = "upload_owner"
    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"))


class ParseBatch(Base):
    """一次批量上传：包含多份试卷，每份各自一个解析任务。"""

    __tablename__ = "parse_batch"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ParseJob(Base):
    __tablename__ = "parse_job"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    # eval：评测时重新解析产生的任务，不出现在任务列表、不参与查重
    kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_count: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int] = mapped_column(Integer)
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / docx / image
    file_keys: Mapped[list[Any]] = mapped_column(JSON)
    # 文件内容指纹（各文件 SHA-256 按顺序再取 SHA-256），用于识别同一份试卷重复入库；旧任务在入库时补算
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    # 难度来源：baseline 按题位估算 / ai 大模型评估 / manual 老师调整
    difficulty_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    block_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    regions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    images: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 题目内图片的存储 key
    duplicate_of: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="draft")
    # 相似题检索用的文本向量（启用 EMBEDDING_MODEL 时）
    embedding: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
    """校本题库：入库快照、个人归属与审核记录。"""

    __tablename__ = "bank_question"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    embedding: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 出处：原卷文件名、题号、页码（试卷名称、学年、地区等在 meta 中）
    source_file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)
if not _settings.db_url.startswith("postgresql"):
    raise RuntimeError("DATABASE_URL 须为 PostgreSQL 地址，如 postgresql+psycopg://user:pass@host:5432/fg_quiz")
# 会话时区固定为 UTC，读出的时间与数据库服务器的时区设置无关；pool_pre_ping 在数据库重启后自动丢弃失效连接
# 每个执行槽都可能同时用到连接，另留给网页请求
engine = create_engine(_settings.db_url, connect_args={"options": "-c timezone=UTC"}, pool_pre_ping=True,
                       pool_size=max(5, _settings.worker_concurrency + 5))

SessionLocal = sessionmaker(engine, expire_on_commit=False)


class KnowledgeTree(Base):
    """知识树：某学科、学段、教材的知识点体系。"""

    __tablename__ = "knowledge_tree"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(16), index=True)
    stage: Mapped[str] = mapped_column(String(8))
    textbook: Mapped[str] = mapped_column(String(64), default="")
    # 内置知识树：同学科有正式导入的知识树时优先使用后者
    builtin: Mapped[bool] = mapped_column(default=False)
    # 内置知识树的来源文件名与内容版本，文件更新后启动时自动替换
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    builtin_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_node"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tree_id: Mapped[str] = mapped_column(ForeignKey("knowledge_tree.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # 完整路径，如「第一章 集合与常用逻辑用语 / 1.3 集合的基本运算 / 交集」
    path: Mapped[str] = mapped_column(Text)
    level: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    is_leaf: Mapped[bool] = mapped_column(default=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    embedding: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EvalSample(Base):
    """评测样本：老师核对后的结果作为标准答案（设为样本时的快照）。"""

    __tablename__ = "eval_sample"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_keys: Mapped[list[Any]] = mapped_column(JSON)
    gold: Mapped[dict[str, Any]] = mapped_column(JSON)  # {meta, questions: [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvalRun(Base):
    __tablename__ = "eval_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    school_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(8))  # queued / running / done / failed
    sample_ids: Mapped[list[Any]] = mapped_column(JSON)
    done: Mapped[int] = mapped_column(Integer, default=0)
    # 当次配置快照（解析引擎、模型、知识树等），便于对比不同配置
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    details: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppMeta(Base):
    """应用级键值，用于记录一次性数据迁移是否已执行。"""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


def migrate_coef_semantics(eng=None) -> int:  # noqa: ANN001
    """难度系数由「预估得分率（越低越难）」改为「越高越难」：旧数据一次性换算为 1 − 旧值。返回换算的行数。"""
    eng = eng or engine
    with eng.begin() as conn:
        done = conn.execute(text("SELECT value FROM app_meta WHERE key = 'coef_semantics'")).scalar()
        if done == "difficulty":
            return 0
        n = 0
        for table in ("draft_question", "bank_question"):
            # PostgreSQL 的 ROUND(x, 2) 只接受 numeric
            n += conn.execute(text(f"UPDATE {table} SET coef = ROUND(CAST(1 - coef AS NUMERIC), 2)")).rowcount or 0
        conn.execute(text("INSERT INTO app_meta (key, value) VALUES ('coef_semantics', 'difficulty')"))
    return n


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
    migrate_coef_semantics()


def get_session(request: Request) -> Iterator[Session]:
    with SessionLocal() as s:
        from .auth import scope_session
        scope_session(s, request.state.user)
        yield s
