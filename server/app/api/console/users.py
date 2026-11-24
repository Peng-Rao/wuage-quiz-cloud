"""管理后台：题库账号管理（角色、学科授权、停用、强制下线）与在线状态。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ...auth import hash_password
from ...console import audit, require
from ...db import ConsoleAccount, LoginSession, ParseJob, SessionLocal, User, utcnow
from ...schemas import UtcDatetime
from ..auth import ONLINE_WINDOW, UserCreate, UserOut, UserWrite, remove_user, validate_write

router = APIRouter(prefix="/api/admin/users")
can_manage = require("users")


class ConsoleUserOut(UserOut):
    is_online: bool
    last_seen_at: UtcDatetime | None
    sessions: int
    uploads: int


@router.get("", response_model=list[ConsoleUserOut])
def list_users(_: ConsoleAccount = Depends(can_manage)):
    now = utcnow()
    with SessionLocal() as s:
        live = select(LoginSession.user_id, func.count().label("n"),
                      func.count().filter(LoginSession.last_seen_at >= now - ONLINE_WINDOW).label("online")) \
            .where(LoginSession.expires_at > now).group_by(LoginSession.user_id).subquery()
        uploads = select(ParseJob.owner_id, func.count().label("n")).where(ParseJob.kind.is_(None)) \
            .group_by(ParseJob.owner_id).subquery()
        rows = s.execute(select(User, live.c.n, live.c.online, uploads.c.n)
                         .outerjoin(live, live.c.user_id == User.id)
                         .outerjoin(uploads, uploads.c.owner_id == User.id).order_by(User.username))
        return [ConsoleUserOut(**UserOut.model_validate(u).model_dump(), is_online=bool(u.active and online),
                               last_seen_at=u.last_seen_at, sessions=n or 0, uploads=up or 0)
                for u, n, online, up in rows]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, actor: ConsoleAccount = Depends(can_manage)):
    validate_write(body)
    with SessionLocal() as s:
        user = User(id=uuid.uuid4().hex, username=body.username.lower(), display_name=body.display_name,
                    password_hash=hash_password(body.password), role=body.role, subjects=body.subjects,
                    active=body.active)
        s.add(user)
        audit(s, actor, "user.create", user.username, {"role": body.role, "subjects": body.subjects})
        try:
            s.commit()
        except IntegrityError:
            raise HTTPException(409, "账号已存在") from None
        return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserWrite, actor: ConsoleAccount = Depends(can_manage)):
    validate_write(body)
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise HTTPException(404, "用户不存在")
        user.display_name, user.role, user.subjects, user.active = body.display_name, body.role, body.subjects, body.active
        if body.password:
            user.password_hash = hash_password(body.password)
        # 与题库内的账号管理一致：修改后需重新登录，新的权限立即生效
        s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        audit(s, actor, "user.update", user.username, {"role": body.role, "subjects": body.subjects,
                                                       "active": body.active, "passwordReset": bool(body.password)})
        s.commit()
        return UserOut.model_validate(user)


@router.post("/{user_id}/sign-out", status_code=204)
def sign_out(user_id: str, actor: ConsoleAccount = Depends(can_manage)):
    """强制下线：删除该账号的全部登录会话。"""
    with SessionLocal() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        s.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
        audit(s, actor, "user.sign_out", user.username)
        s.commit()


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, actor: ConsoleAccount = Depends(can_manage)):
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.id == user_id).with_for_update())
        if not user:
            raise HTTPException(404, "用户不存在")
        if user.role == "admin" and (s.scalar(select(func.count()).select_from(User).where(
                User.role == "admin", User.active.is_(True), User.id != user.id)) or 0) == 0:
            raise HTTPException(400, "这是题库中唯一启用的管理员，不能删除")
        audit(s, actor, "user.delete", user.username)
        remove_user(s, user)
        s.commit()
