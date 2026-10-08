from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
import bcrypt
from ..db import get_db
from .. import models
from ..auth import get_current_user, require_role, log_op

router = APIRouter(prefix="/api/auth", tags=["auth"])

def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_pw(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())

class LoginIn(BaseModel):
    username: str
    password: str

class UserIn(BaseModel):
    username: str
    password: str
    role: str = "operator"

@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_pw(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    request.session["user_id"] = user.id
    log_op(db, user.id, "login", f"用户 {user.username} 登录")
    return {"user": {"id": user.id, "username": user.username, "role": user.role}}

@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}

@router.get("/me")
def me(user: models.User = Depends(get_current_user)):
    return {"user": {"id": user.id, "username": user.username, "role": user.role}}

@router.get("/users")
def list_users(_: models.User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    users = db.query(models.User).order_by(models.User.id).all()
    return {"users": [{"id": u.id, "username": u.username, "role": u.role,
                       "created_at": u.created_at.isoformat() if u.created_at else None} for u in users]}

@router.post("/users")
def create_user(data: UserIn, user: models.User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    if data.role not in ("admin", "operator", "viewer"):
        raise HTTPException(400, "角色非法")
    if db.query(models.User).filter(models.User.username == data.username).first():
        raise HTTPException(400, "用户名已存在")
    u = models.User(username=data.username, password_hash=hash_pw(data.password), role=data.role)
    db.add(u); db.commit(); db.refresh(u)
    log_op(db, user.id, "user.create", f"创建用户 {data.username}({data.role})")
    return {"user": {"id": u.id, "username": u.username, "role": u.role}}

@router.delete("/users/{uid}")
def delete_user(uid: int, user: models.User = Depends(require_role("admin")),
                db: Session = Depends(get_db)):
    if uid == user.id:
        raise HTTPException(400, "不能删除自己")
    db.query(models.User).filter(models.User.id == uid).delete()
    db.commit()
    log_op(db, user.id, "user.delete", f"删除用户 id={uid}")
    return {"ok": True}
