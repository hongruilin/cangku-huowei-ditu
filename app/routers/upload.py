import os, secrets
from datetime import datetime
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api", tags=["upload"])

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

class DeleteIn(BaseModel):
    key: str = None

@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 user: models.User = Depends(require_role("operator")),
                 db: Session = Depends(get_db)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "只支持图片文件")
    if "heic" in file.content_type.lower() or "heif" in file.content_type.lower():
        raise HTTPException(400, "暂不支持 HEIC 格式，请用 JPEG/PNG")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "图片太大（超过15MB）")
    ext = ".png" if file.filename and file.filename.lower().endswith(".png") else ".jpg"
    day = datetime.now().strftime("%Y/%m")
    d = os.path.join(UPLOAD_DIR, day)
    os.makedirs(d, exist_ok=True)
    key = f"{day}/{secrets.token_hex(12)}{ext}"
    with open(os.path.join(UPLOAD_DIR, key), "wb") as f:
        f.write(data)
    log_op(db, user.id, "image.upload", f"上传图片 {key}({len(data)//1024}KB)")
    return {"key": key, "url": f"/uploads/{key}"}

@router.delete("/upload")
def delete_image(data: DeleteIn, user: models.User = Depends(require_role("operator")),
                 db: Session = Depends(get_db)):
    if data.key:
        p = os.path.join(UPLOAD_DIR, data.key)
        # 防目录穿越
        if os.path.commonpath([os.path.abspath(p), os.path.abspath(UPLOAD_DIR)]) == os.path.abspath(UPLOAD_DIR):
            try: os.remove(p)
            except FileNotFoundError: pass
        log_op(db, user.id, "image.delete", f"删除图片 {data.key}")
    return {"ok": True}

@router.get("/logs")
def list_logs(action: str = None, limit: int = 100,
             user: models.User = Depends(require_role("viewer")),
             db: Session = Depends(get_db)):
    q = db.query(models.OpLog, models.User.username)\
          .outerjoin(models.User, models.User.id == models.OpLog.user_id)\
          .order_by(models.OpLog.id.desc())
    if action:
        q = q.filter(models.OpLog.action == action)
    rows = q.limit(max(1, min(500, limit))).all()
    return {"logs": [{"id": l.id, "user_id": l.user_id, "username": un,
                      "action": l.action, "detail": l.detail,
                      "created_at": l.created_at.isoformat() if l.created_at else None}
                     for l, un in rows]}
