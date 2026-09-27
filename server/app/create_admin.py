"""Run locally: uv run python -m app.create_admin USERNAME"""
import argparse
import getpass
import uuid

from sqlalchemy import select

from .auth import hash_password
from .db import SessionLocal, User, init_db
from .api.auth import UserCreate


def main():
    parser = argparse.ArgumentParser(description="创建首个管理员（密码不回显）")
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("管理员密码（至少 10 位）: ")
    if password != getpass.getpass("再次输入密码: "):
        raise SystemExit("两次密码不一致")
    data = UserCreate(username=args.username, display_name="管理员", password=password, role="admin")
    init_db()
    with SessionLocal() as s:
        if s.scalar(select(User.id).where(User.username == data.username.lower())):
            raise SystemExit("账号已存在，请在账号管理中重置密码")
        s.add(User(id=uuid.uuid4().hex, username=data.username.lower(), display_name=data.display_name,
                   password_hash=hash_password(password), role="admin", subjects=[], active=True))
        s.commit()
    print("管理员已创建。请使用该账号登录。")


if __name__ == "__main__":
    main()
