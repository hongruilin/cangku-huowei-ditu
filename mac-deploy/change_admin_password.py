#!/usr/bin/env python3
"""在 app 容器内修改管理员密码（已有 admin 时改 ADMIN_PASS 环境变量无效）。

用法（在部署目录执行）:
  docker compose exec app python mac-deploy/change_admin_password.py
然后按提示输入新密码（两次）。

或非交互:
  NEW_PASS='新密码' docker compose exec -e NEW_PASS app python mac-deploy/change_admin_password.py
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bcrypt

from app.db import SessionLocal
from app import models


def main() -> None:
    username = os.getenv("ADMIN_USER", "admin")
    pw = os.getenv("NEW_PASS") or getpass.getpass("新密码: ")
    if not os.getenv("NEW_PASS"):
        if pw != getpass.getpass("再输一次: "):
            print("两次输入不一致，未修改")
            sys.exit(1)
    if len(pw) < 8:
        print("密码至少 8 位，未修改")
        sys.exit(1)
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == username).first()
        if not user:
            print(f"用户 {username} 不存在，未修改")
            sys.exit(1)
        user.password_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        db.commit()
        print(f"已修改 {username} 的密码，现有登录会话不受影响，新密码下次登录生效")
    finally:
        db.close()


if __name__ == "__main__":
    main()
