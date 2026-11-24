"""创建管理后台 root 账号（邮箱 + 密码，密码不回显）：uv run python -m app.create_root EMAIL"""
import argparse
import getpass

from .console import create_root
from .db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="创建管理后台 root 账号")
    parser.add_argument("email")
    parser.add_argument("--name", default="Root", help="显示名称")
    args = parser.parse_args()
    password = getpass.getpass("root 密码（至少 10 位）: ")
    if password != getpass.getpass("再次输入密码: "):
        raise SystemExit("两次密码不一致")
    init_db()
    try:
        create_root(args.email, password, args.name)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print("root 账号已创建。请打开 /admin 用该邮箱登录。")


if __name__ == "__main__":
    main()
