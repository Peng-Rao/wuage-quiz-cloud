import secrets
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..auth import COOKIE, admin, check_origin, current_user, hash_password, staff, token_hash, verify_password
from ..config import get_settings
from ..db import LoginSession, LoginThrottle, SessionLocal, User, utcnow
from ..pipeline.classify import ALL_SUBJECTS
from ..schemas import Model

router = APIRouter(prefix="/api")
DUMMY_HASH = hash_password("not-a-real-password")


class UserOut(Model):
    id: str
    username: str
    display_name: str
    role: Literal["admin", "leader", "member"]
    subjects: list[str]
    active: bool


class Login(Model):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserWrite(Model):
    display_name: str = Field(min_length=1, max_length=64)
    role: Literal["admin", "leader", "member"]
    subjects: list[str] = Field(default_factory=list)
    active: bool = True
    password: str | None = Field(None, min_length=10, max_length=128)

    @field_validator("subjects")
    @classmethod
    def valid_subjects(cls, value):
        if any(v not in ALL_SUBJECTS for v in value):
            raise ValueError("未知学科")
        return sorted(set(value))

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("姓名不能为空")
        return value.strip()


class UserCreate(UserWrite):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
    password: str = Field(min_length=10, max_length=128)


def out(user):
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=UserOut)
def login(body: Login, request: Request, response: Response):
    check_origin(request)
    name = body.username.strip().lower()
    now = utcnow()
    with SessionLocal() as s:
        s.execute(delete(LoginThrottle).where(LoginThrottle.expires_at < now))
        s.execute(delete(LoginSession).where(LoginSession.expires_at < now))
        # Persist throttling across restarts; throttle both account and client address.
        keys = [token_hash("user:" + name), token_hash("ip:" + (request.client.host if request.client else "unknown"))]
        for key, limit in zip(keys, (10, 60)):
            counter = s.get(LoginThrottle, key)
            if counter and counter.attempts >= limit:
                raise HTTPException(429, "登录尝试过多，请 15 分钟后重试")
            if not counter:
                counter = LoginThrottle(key=key, attempts=0, expires_at=now + timedelta(minutes=15))
                s.add(counter)
            counter.attempts += 1
        s.commit()
        user = s.scalar(select(User).where(User.username == name))
        valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
        if not valid or not user or not user.active:
            raise HTTPException(401, "账号或密码错误，或账号已停用")
        s.execute(delete(LoginThrottle).where(LoginThrottle.key == keys[0]))
        token = secrets.token_urlsafe(32)
        old = request.cookies.get(COOKIE)
        if old:
            s.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old)))
        s.add(LoginSession(token_hash=token_hash(token), user_id=user.id,
                           expires_at=now + timedelta(hours=get_settings().session_hours)))
        s.commit()
        response.set_cookie(COOKIE, token, httponly=True, secure=get_settings().cookie_secure,
                            samesite="lax", max_age=get_settings().session_hours * 3600, path="/")
        response.headers["Cache-Control"] = "no-store"
        return out(user)


@router.get("/auth/me", response_model=UserOut)
def me(response: Response, user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return out(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    check_origin(request)
    with SessionLocal() as s:
        s.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies.get(COOKIE, ""))))
        s.commit()
    response.delete_cookie(COOKIE, path="/", secure=get_settings().cookie_secure, httponly=True, samesite="lax")


@router.get("/users", response_model=list[UserOut])
def users(_: User = Depends(admin)):
    with SessionLocal() as s:
        return [out(u) for u in s.scalars(select(User).order_by(User.username))]


@router.get("/review-recipients", response_model=list[UserOut])
def recipients(user: User = Depends(staff)):
    with SessionLocal() as s:
        rows = s.scalars(select(User).where(User.active.is_(True)).order_by(User.username))
        return [out(u) for u in rows if user.role == "admin" or
                (u.role == "member" and set(u.subjects) & set(user.subjects))]


def validate_write(body):
    if body.role != "admin" and not body.subjects:
        raise HTTPException(422, "组长和普通用户至少需要一个学科")


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, _: User = Depends(admin)):
    validate_write(body)
    with SessionLocal() as s:
        user = User(id=uuid.uuid4().hex, username=body.username.lower(), display_name=body.display_name,
                    password_hash=hash_password(body.password), role=body.role, subjects=body.subjects, active=body.active)
        s.add(user)
        try:
            s.commit()
        except IntegrityError:
            raise HTTPException(409, "账号已存在")
        return out(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserWrite, actor: User = Depends(admin)):
    validate_write(body)
    with SessionLocal() as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        # Prevent accidental self-lockout, including concurrent last-admin changes.
        if actor.id == user.id and (body.role != "admin" or not body.active):
            raise HTTPException(400, "不能停用自己或降低自己的管理员权限")
        user.display_name, user.role, user.subjects, user.active = body.display_name, body.role, body.subjects, body.active
        if body.password:
            user.password_hash = hash_password(body.password)
        s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        s.commit()
        return out(user)
