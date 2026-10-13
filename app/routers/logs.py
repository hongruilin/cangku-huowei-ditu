from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api", tags=["logs"])

@router.get("/op-logs")
def list_logs(user: models.User = Depends(require_role("viewer")),
              db: Session = Depends(get_db),
              q: str = Query("", max_length=64),
              user_id: int = None,
              page: int = Query(1, ge=1),
              per_page: int = Query(50, ge=1, le=200)):
    """操作日志列表（倒序）：id/用户/动作/明细/时间，支持关键词和用户过滤"""
    qry = db.query(models.OpLog, models.User.username)\
            .outerjoin(models.User, models.User.id == models.OpLog.user_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter((models.OpLog.action.like(like)) | (models.OpLog.detail.like(like)))
    if user_id:
        qry = qry.filter(models.OpLog.user_id == user_id)
    total = qry.count()
    rows = qry.order_by(models.OpLog.id.desc())\
              .offset((page - 1) * per_page).limit(per_page).all()
    items = []
    for lg, uname in rows:
        items.append({
            "id": lg.id,
            "user_id": lg.user_id,
            "username": uname if uname else ("AI/MCP" if lg.user_id is None else "—"),
            "action": lg.action,
            "detail": lg.detail or "",
            "created_at": lg.created_at.isoformat() if lg.created_at else None,
        })
    return {"total": total, "page": page, "per_page": per_page, "items": items}

@router.delete("/op-logs")
def clear_logs(user: models.User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    """清空全部操作日志（管理员），清空后留一条记录说明是谁清空的"""
    db.query(models.OpLog).delete()
    db.commit()
    log_op(db, user.id, "logs.clear", "清空操作日志")
    return {"ok": True}
