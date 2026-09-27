"""Cookie sessions and request-scoped data permissions. Workers use unscoped sessions."""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, with_loader_criteria

from .config import get_settings
from .db import BankQuestion, DraftQuestion, KnowledgeTree, LoginSession, ParseJob, SessionLocal, User, utcnow

COOKIE = "quiz_session"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    _, salt, expected = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), *get_settings().cors_origins}
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in allowed):
        raise HTTPException(403, "请求来源不受信任")


def as_utc(d: datetime) -> datetime:
    """SQLite 读出的是不带时区的 UTC 时间；PostgreSQL 读出的是带时区（数据库会话时区）的时间。"""
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def current_user(request: Request) -> User:
    check_origin(request)
    with SessionLocal() as s:
        session = s.get(LoginSession, token_hash(request.cookies.get(COOKIE, "")))
        user = s.get(User, session.user_id) if session else None
        if not session or as_utc(session.expires_at) <= utcnow() or not user or not user.active:
            raise HTTPException(401, "请先登录或重新登录")
        s.expunge(user)
    request.state.user = user
    return user


def staff(user: User = Depends(current_user)) -> User:
    if user.role not in {"admin", "leader"}:
        raise HTTPException(403, "仅管理员和组长可使用此功能")
    return user


def admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可使用此功能")
    return user


def require_subject(user: User, subject: str) -> None:
    if user.role != "admin" and subject not in user.subjects:
        raise HTTPException(403, "无权访问该学科")


def scope_session(s: Session, user: User) -> None:
    """Filter entities, aggregates and pagination together, including indirect lookups."""
    s.info["user"] = user
    if user.role == "admin":
        return
    bank = BankQuestion.__table__
    if user.role == "leader":
        bank_rule = BankQuestion.meta["subject"].as_string().in_(user.subjects)
        job_rule = func.coalesce(func.nullif(ParseJob.meta["subject"].as_string(), ""), ParseJob.subject_scope).in_(user.subjects)
        job_ids = select(ParseJob.__table__.c.id).where(
            func.coalesce(func.nullif(ParseJob.__table__.c.meta["subject"].as_string(), ""),
                          ParseJob.__table__.c.subject_scope).in_(user.subjects))
        draft_rule = DraftQuestion.job_id.in_(job_ids)
        tree_rule = KnowledgeTree.subject.in_(user.subjects)
    else:
        allowed = (bank.c.owner_id == user.id) & bank.c.reviewed_at.is_not(None) & bank.c.meta["subject"].as_string().in_(user.subjects)
        bank_rule = (BankQuestion.owner_id == user.id) & BankQuestion.reviewed_at.is_not(None) & BankQuestion.meta["subject"].as_string().in_(user.subjects)
        job_rule = ParseJob.id.in_(select(bank.c.source_job_id).where(allowed))
        draft_rule = DraftQuestion.id.in_(select(bank.c.source_draft_id).where(allowed))
        tree_rule = KnowledgeTree.subject.in_(select(bank.c.meta["subject"].as_string()).where(allowed))

    @event.listens_for(s, "do_orm_execute")
    def filter_rows(state):
        if state.is_select:
            state.statement = state.statement.options(
                with_loader_criteria(BankQuestion, bank_rule, include_aliases=True),
                with_loader_criteria(ParseJob, job_rule, include_aliases=True),
                with_loader_criteria(DraftQuestion, draft_rule, include_aliases=True),
                with_loader_criteria(KnowledgeTree, tree_rule, include_aliases=True),
            )
