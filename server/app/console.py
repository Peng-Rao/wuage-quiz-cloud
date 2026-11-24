"""管理后台的账号、会话与权限。与题库账号（app_user）相互独立：后台账号用邮箱登录，
root 拥有全部权限并为其他后台账号授权；普通后台账号只能访问被授予的模块。"""

import logging
import re
import uuid
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .auth import as_utc, check_origin, hash_password, token_hash
from .config import get_settings
from .db import ConsoleAccount, ConsoleAudit, ConsoleSession, SessionLocal, utcnow

log = logging.getLogger(__name__)

COOKIE = "quiz_console"
COOKIE_PATH = "/api/admin"
# 模块权限；root 隐含全部权限，并且只有 root 能管理后台账号与查看操作记录
PERMISSIONS: dict[str, str] = {
    "exams": "试卷管理",
    "users": "用户管理",
    "keys": "密钥配置",
    "ai": "价格与用量",
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEEN_EVERY = timedelta(seconds=60)


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not EMAIL.match(value):
        raise ValueError("邮箱格式不正确")
    return value


def can(account: ConsoleAccount, permission: str) -> bool:
    return account.is_root or permission in (account.permissions or [])


def console_account(request: Request) -> ConsoleAccount:
    check_origin(request)
    now = utcnow()
    with SessionLocal() as s:
        session = s.get(ConsoleSession, token_hash(request.cookies.get(COOKIE, "")))
        account = s.get(ConsoleAccount, session.account_id) if session else None
        if not session or as_utc(session.expires_at) <= now or not account or not account.active:
            raise HTTPException(401, "请先登录管理后台")
        if account.last_seen_at is None or as_utc(account.last_seen_at) < now - SEEN_EVERY:
            s.execute(update(ConsoleAccount).where(ConsoleAccount.id == account.id).values(last_seen_at=now))
            s.commit()
        s.expunge(account)
    request.state.console = account
    return account


def require(permission: str):  # noqa: ANN201
    def check(account: ConsoleAccount = Depends(console_account)) -> ConsoleAccount:
        if not can(account, permission):
            raise HTTPException(403, f"没有「{PERMISSIONS[permission]}」权限，请联系 root 账号授权")
        return account
    return check


def root(account: ConsoleAccount = Depends(console_account)) -> ConsoleAccount:
    if not account.is_root:
        raise HTTPException(403, "仅 root 账号可使用此功能")
    return account


def audit(s: Session, actor: ConsoleAccount | str, action: str, target: str | None = None,
          detail: dict[str, Any] | None = None) -> None:
    """记录一条后台操作（随调用方的事务提交）。detail 中不要放密钥、密码。"""
    s.add(ConsoleAudit(actor=actor if isinstance(actor, str) else actor.email, action=action,
                       target=(target or "")[:512] or None, detail=detail, created_at=utcnow()))


def create_root(email: str, password: str, display_name: str = "Root") -> ConsoleAccount:
    email = normalize_email(email)
    if len(password) < 10:
        raise ValueError("密码至少 10 位")
    with SessionLocal() as s:
        if s.scalar(select(ConsoleAccount.id).where(ConsoleAccount.email == email)):
            raise ValueError("该邮箱已存在")
        account = ConsoleAccount(id=uuid.uuid4().hex, email=email, display_name=display_name,
                                 password_hash=hash_password(password), is_root=True, permissions=[], active=True)
        s.add(account)
        audit(s, "system", "account.create_root", email)
        s.commit()
        return account


def ensure_root_from_env() -> None:
    """还没有 root 账号时，用 CONSOLE_ROOT_EMAIL / CONSOLE_ROOT_PASSWORD 创建。已有 root 时不做任何事。"""
    st = get_settings()
    if not (st.console_root_email and st.console_root_password):
        return
    with SessionLocal() as s:
        if s.scalar(select(ConsoleAccount.id).where(ConsoleAccount.is_root.is_(True)).limit(1)):
            return
    try:
        create_root(st.console_root_email, st.console_root_password)
        log.info("已按 CONSOLE_ROOT_EMAIL 创建管理后台 root 账号")
    except ValueError as e:
        log.warning("无法创建管理后台 root 账号：%s", e)
