"""管理后台接口（/api/admin/*）：独立的邮箱账号登录，按模块授权。"""

from fastapi import APIRouter

from . import ai, auth, exams, settings, users

router = APIRouter()
for _module in (auth, exams, users, settings, ai):
    router.include_router(_module.router)
