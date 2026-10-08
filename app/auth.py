from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from .db import get_db
from . import models

def get_current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(401, "未登录")
    user = db.query(models.User).filter(models.User.id == uid).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user

def require_role(min_role: str):
    rank = {"viewer": 1, "operator": 2, "admin": 3}
    def checker(user: models.User = Depends(get_current_user)):
        if rank.get(user.role, 0) < rank.get(min_role, 0):
            raise HTTPException(403, "权限不足")
        return user
    return checker

def log_op(db: Session, user_id, action: str, detail: str = None):
    db.add(models.OpLog(user_id=user_id, action=action, detail=detail))
    db.commit()
