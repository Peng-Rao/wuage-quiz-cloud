"""管理后台：登录、后台账号授权（仅 root）、操作记录与概览。"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, func, select

from ...auth import check_origin, hash_password, token_hash, verify_password
from ...config import get_settings
from ...console import (
    COOKIE, COOKIE_PATH, PERMISSIONS, audit, console_account, normalize_email, root,
)
from ...db import (
    AiUsage, BankQuestion, ConsoleAccount, ConsoleAudit, ConsoleSession, LoginSession, LoginThrottle, ParseJob,
    SessionLocal, User, utcnow,
)
from ...schemas import Model, UtcDatetime
from ..auth import DUMMY_HASH, ONLINE_WINDOW

router = APIRouter(prefix="/api/admin")


class AccountOut(Model):
    id: str
    email: str
    display_name: str
    is_root: bool
    permissions: list[str]
    active: bool
    created_at: UtcDatetime
    last_login_at: UtcDatetime | None = None
    last_seen_at: UtcDatetime | None = None


class Login(Model):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(Model):
    current: str = Field(min_length=1, max_length=128)
    new: str = Field(min_length=10, max_length=128)


class AccountWrite(Model):
    display_name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)
    active: bool = True
    password: str | None = Field(None, min_length=10, max_length=128)

    @field_validator("permissions")
    @classmethod
    def known(cls, value):
        if any(p not in PERMISSIONS for p in value):
            raise ValueError("未知权限")
        return sorted(set(value))

    @field_validator("display_name")
    @classmethod
    def name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class AccountCreate(AccountWrite):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


def out(a: ConsoleAccount) -> AccountOut:
    return AccountOut.model_validate(a).model_copy(
        update={"permissions": list(PERMISSIONS) if a.is_root else list(a.permissions or [])})


def _set_cookie(response: Response, token: str) -> None:
    hours = get_settings().console_session_hours
    response.set_cookie(COOKIE, token, httponly=True, secure=get_settings().cookie_secure, samesite="strict",
                        max_age=hours * 3600, path=COOKIE_PATH)


# ---------------- 登录 ----------------

@router.post("/auth/login", response_model=AccountOut)
def login(body: Login, request: Request, response: Response):
    check_origin(request)
    email = body.email.strip().lower()
    now = utcnow()
    with SessionLocal() as s:
        s.execute(delete(LoginThrottle).where(LoginThrottle.expires_at < now))
        s.execute(delete(ConsoleSession).where(ConsoleSession.expires_at < now))
        keys = [token_hash("console:" + email),
                token_hash("console-ip:" + (request.client.host if request.client else "unknown"))]
        for key, limit in zip(keys, (8, 40)):
            counter = s.get(LoginThrottle, key)
            if counter and counter.attempts >= limit:
                raise HTTPException(429, "登录尝试过多，请 15 分钟后重试")
            if not counter:
                counter = LoginThrottle(key=key, attempts=0, expires_at=now + timedelta(minutes=15))
                s.add(counter)
            counter.attempts += 1
        s.commit()
        account = s.scalar(select(ConsoleAccount).where(ConsoleAccount.email == email))
        valid = verify_password(body.password, account.password_hash if account else DUMMY_HASH)
        if not valid or not account or not account.active:
            raise HTTPException(401, "邮箱或密码错误，或账号已停用")
        s.execute(delete(LoginThrottle).where(LoginThrottle.key == keys[0]))
        old = request.cookies.get(COOKIE)
        if old:
            s.execute(delete(ConsoleSession).where(ConsoleSession.token_hash == token_hash(old)))
        token = secrets.token_urlsafe(32)
        s.add(ConsoleSession(token_hash=token_hash(token), account_id=account.id, created_at=now,
                             expires_at=now + timedelta(hours=get_settings().console_session_hours)))
        account.last_login_at = account.last_seen_at = now
        audit(s, account, "auth.login")
        s.commit()
        _set_cookie(response, token)
        return out(account)


@router.get("/auth/me", response_model=AccountOut)
def me(account: ConsoleAccount = Depends(console_account)):
    return out(account)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    check_origin(request)
    with SessionLocal() as s:
        s.execute(delete(ConsoleSession).where(ConsoleSession.token_hash == token_hash(request.cookies.get(COOKIE, ""))))
        s.commit()
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=get_settings().cookie_secure, httponly=True,
                           samesite="strict")


@router.put("/auth/password", status_code=204)
def change_password(body: PasswordChange, request: Request, account: ConsoleAccount = Depends(console_account)):
    """修改自己的密码：保留当前会话，其他设备上的会话全部失效。"""
    with SessionLocal() as s:
        row = s.get(ConsoleAccount, account.id)
        if not verify_password(body.current, row.password_hash):
            raise HTTPException(400, "当前密码不正确")
        row.password_hash = hash_password(body.new)
        current = token_hash(request.cookies.get(COOKIE, ""))
        s.execute(delete(ConsoleSession).where(ConsoleSession.account_id == account.id,
                                               ConsoleSession.token_hash != current))
        audit(s, account, "auth.password")
        s.commit()


# ---------------- 后台账号授权（仅 root） ----------------

@router.get("/accounts", response_model=list[AccountOut])
def accounts(_: ConsoleAccount = Depends(root)):
    with SessionLocal() as s:
        rows = s.scalars(select(ConsoleAccount).order_by(ConsoleAccount.is_root.desc(), ConsoleAccount.email))
        return [out(a) for a in rows]


@router.get("/permissions")
def permissions(_: ConsoleAccount = Depends(console_account)) -> dict[str, str]:
    return PERMISSIONS


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, actor: ConsoleAccount = Depends(root)):
    with SessionLocal() as s:
        if s.scalar(select(ConsoleAccount.id).where(ConsoleAccount.email == body.email)):
            raise HTTPException(409, "该邮箱已存在")
        account = ConsoleAccount(id=uuid.uuid4().hex, email=body.email, display_name=body.display_name,
                                 password_hash=hash_password(body.password), is_root=False,
                                 permissions=body.permissions, active=body.active, created_at=utcnow())
        s.add(account)
        audit(s, actor, "account.create", body.email, {"permissions": body.permissions, "active": body.active})
        s.commit()
        return out(account)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: str, body: AccountWrite, actor: ConsoleAccount = Depends(root)):
    with SessionLocal() as s:
        account = s.get(ConsoleAccount, account_id)
        if account is None:
            raise HTTPException(404, "账号不存在")
        if account.is_root and not body.active:
            raise HTTPException(400, "不能停用 root 账号")
        revoke = (not account.is_root and (set(account.permissions or []) != set(body.permissions)
                                           or account.active != body.active)) or bool(body.password)
        account.display_name = body.display_name
        if not account.is_root:
            account.permissions, account.active = body.permissions, body.active
        if body.password:
            account.password_hash = hash_password(body.password)
        if revoke:
            # 权限或密码变化后需重新登录；root 修改自己的密码时也退出其他会话
            s.execute(delete(ConsoleSession).where(ConsoleSession.account_id == account.id))
        audit(s, actor, "account.update", account.email,
              {"permissions": account.permissions, "active": account.active, "passwordReset": bool(body.password)})
        s.commit()
        return out(account)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: str, actor: ConsoleAccount = Depends(root)):
    with SessionLocal() as s:
        account = s.get(ConsoleAccount, account_id)
        if account is None:
            raise HTTPException(404, "账号不存在")
        if account.is_root:
            raise HTTPException(400, "不能删除 root 账号")
        s.execute(delete(ConsoleSession).where(ConsoleSession.account_id == account.id))
        s.delete(account)
        audit(s, actor, "account.delete", account.email)
        s.commit()


class AuditOut(Model):
    id: int
    actor: str
    action: str
    target: str | None
    detail: dict[str, Any] | None
    created_at: UtcDatetime


class AuditPage(Model):
    items: list[AuditOut]
    total: int


@router.get("/audit", response_model=AuditPage)
def audit_log(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              action: str | None = Query(None, max_length=64), _: ConsoleAccount = Depends(root)):
    with SessionLocal() as s:
        q = select(ConsoleAudit)
        if action:
            q = q.where(ConsoleAudit.action.startswith(action))
        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = s.scalars(q.order_by(ConsoleAudit.id.desc()).limit(limit).offset(offset))
        return AuditPage(items=[AuditOut.model_validate(r) for r in rows], total=total)


# ---------------- 概览 ----------------

@router.get("/overview")
def overview(_: ConsoleAccount = Depends(console_account)) -> dict[str, Any]:
    from ...cost_analysis import today
    from ...pricing import BEIJING
    from ...usage import summarize
    from ...worker import worker

    now = utcnow()
    st = get_settings()
    day = today()
    month_start = datetime(day.year, day.month, 1, tzinfo=BEIJING)
    with SessionLocal() as s:
        users = s.execute(select(func.count(), func.count().filter(User.active.is_(True))).select_from(User)).one()
        online = s.scalar(select(func.count(func.distinct(LoginSession.user_id))).join(User, User.id == LoginSession.user_id)
                          .where(LoginSession.expires_at > now, LoginSession.last_seen_at >= now - ONLINE_WINDOW,
                                 User.active.is_(True))) or 0
        by_status = dict(s.execute(select(ParseJob.status, func.count()).where(ParseJob.kind.is_(None))
                                   .group_by(ParseJob.status)).all())
        bank = s.scalar(select(func.count()).select_from(BankQuestion)) or 0
        papers = s.scalar(select(func.count(func.distinct(BankQuestion.source_job_id)))) or 0
        month = summarize(s.scalars(select(AiUsage).where(AiUsage.created_at >= month_start)))
    return {
        "users": {"total": users[0], "active": users[1], "online": online},
        "exams": {"total": sum(by_status.values()), "byStatus": by_status, "papers": papers, "questions": bank},
        "ai": {"monthCost": month.cost, "currency": month.currency, "monthCalls": month.calls,
               "monthTokens": month.total_tokens, "monthPages": month.pages},
        "services": {"llm": st.llm_enabled, "vision": st.vision_enabled, "mineru": bool(st.mineru_token),
                     "embedding": bool(st.embedding_model), "worker": worker.info()},
    }
