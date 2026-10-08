from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api", tags=["warehouses"])

class WarehouseIn(BaseModel):
    name: str

class FloorIn(BaseModel):
    name: str = "1F"
    rows: int = 8
    cols: int = 14

class FloorUpdate(BaseModel):
    name: str = None
    rows: int = None
    cols: int = None

class StatusIn(BaseModel):
    key: str = None
    name: str
    color: str = "#e2e8f0"

class ZoneIn(BaseModel):
    name: str = "区域"
    r0: int = 0; c0: int = 0; r1: int = 0; c1: int = 0

# ---- 仓库 ----
@router.get("/warehouses")
def list_warehouses(user: models.User = Depends(require_role("viewer")),
                    db: Session = Depends(get_db)):
    ws = db.query(models.Warehouse).order_by(models.Warehouse.id).all()
    return {"warehouses": [{"id": w.id, "name": w.name} for w in ws]}

@router.post("/warehouses")
def create_warehouse(data: WarehouseIn, user: models.User = Depends(require_role("operator")),
                     db: Session = Depends(get_db)):
    w = models.Warehouse(name=data.name)
    db.add(w); db.commit(); db.refresh(w)
    log_op(db, user.id, "warehouse.create", f"创建仓库 {data.name}")
    return {"warehouse": {"id": w.id, "name": w.name}}

@router.delete("/warehouses/{wid}")
def delete_warehouse(wid: int, user: models.User = Depends(require_role("admin")),
                     db: Session = Depends(get_db)):
    db.query(models.Warehouse).filter(models.Warehouse.id == wid).delete()
    db.commit()
    log_op(db, user.id, "warehouse.delete", f"删除仓库 id={wid}")
    return {"ok": True}

# ---- 楼层 ----
@router.get("/warehouses/{wid}/floors")
def list_floors(wid: int, user: models.User = Depends(require_role("viewer")),
                db: Session = Depends(get_db)):
    fs = db.query(models.Floor).filter(models.Floor.warehouse_id == wid)\
           .order_by(models.Floor.sort_order, models.Floor.id).all()
    return {"floors": [{"id": f.id, "name": f.name, "rows": f.rows, "cols": f.cols} for f in fs]}

@router.post("/warehouses/{wid}/floors")
def create_floor(wid: int, data: FloorIn, user: models.User = Depends(require_role("operator")),
                 db: Session = Depends(get_db)):
    max_sort = db.query(func.max(models.Floor.sort_order))\
                 .filter(models.Floor.warehouse_id == wid).scalar() or 0
    f = models.Floor(warehouse_id=wid, name=data.name,
                     rows=max(1, min(60, data.rows)), cols=max(1, min(60, data.cols)),
                     sort_order=max_sort + 1)
    db.add(f); db.commit(); db.refresh(f)
    log_op(db, user.id, "floor.create", f"创建楼层 {data.name}(仓库{wid})")
    return {"floor": {"id": f.id, "name": f.name, "rows": f.rows, "cols": f.cols}}

@router.put("/floors/{fid}")
def update_floor(fid: int, data: FloorUpdate, user: models.User = Depends(require_role("operator")),
                 db: Session = Depends(get_db)):
    f = db.query(models.Floor).filter(models.Floor.id == fid).first()
    if not f: raise HTTPException(404, "楼层不存在")
    if data.name is not None: f.name = data.name
    if data.rows is not None: f.rows = max(1, min(60, data.rows))
    if data.cols is not None: f.cols = max(1, min(60, data.cols))
    db.commit()
    log_op(db, user.id, "floor.update", f"更新楼层 id={fid}")
    return {"floor": {"id": f.id, "name": f.name, "rows": f.rows, "cols": f.cols}}

@router.delete("/floors/{fid}")
def delete_floor(fid: int, user: models.User = Depends(require_role("operator")),
                 db: Session = Depends(get_db)):
    db.query(models.Floor).filter(models.Floor.id == fid).delete()
    db.commit()
    log_op(db, user.id, "floor.delete", f"删除楼层 id={fid}")
    return {"ok": True}

# ---- 状态 ----
@router.get("/warehouses/{wid}/statuses")
def list_statuses(wid: int, user: models.User = Depends(require_role("viewer")),
                  db: Session = Depends(get_db)):
    ss = db.query(models.Status).filter(
        (models.Status.warehouse_id.is_(None)) | (models.Status.warehouse_id == wid)
    ).order_by(models.Status.builtin.desc(), models.Status.id).all()
    return {"statuses": [{"id": s.id, "key": s.key, "name": s.name,
                          "color": s.color, "builtin": s.builtin} for s in ss]}

@router.post("/warehouses/{wid}/statuses")
def upsert_status(wid: int, data: StatusIn, user: models.User = Depends(require_role("operator")),
                  db: Session = Depends(get_db)):
    import time
    key = data.key or f"c{int(time.time())}"
    s = db.query(models.Status).filter(
        models.Status.warehouse_id == wid, models.Status.key == key).first()
    if s:
        s.name = data.name; s.color = data.color
    else:
        s = models.Status(warehouse_id=wid, key=key, name=data.name, color=data.color)
        db.add(s)
    db.commit()
    log_op(db, user.id, "status.upsert", f"仓库{wid} 状态 {data.name}")
    return {"status": {"id": s.id, "key": s.key, "name": s.name, "color": s.color}}

@router.delete("/statuses/{sid}")
def delete_status(sid: int, user: models.User = Depends(require_role("operator")),
                  db: Session = Depends(get_db)):
    s = db.query(models.Status).filter(models.Status.id == sid).first()
    if not s: raise HTTPException(404, "状态不存在")
    if s.builtin: raise HTTPException(400, "内置状态不可删除")
    db.delete(s); db.commit()
    log_op(db, user.id, "status.delete", f"删除状态 id={sid}")
    return {"ok": True}

# ---- 区域 ----
@router.get("/floors/{fid}/zones")
def list_zones(fid: int, user: models.User = Depends(require_role("viewer")),
               db: Session = Depends(get_db)):
    zs = db.query(models.Zone).filter(models.Zone.floor_id == fid).order_by(models.Zone.id).all()
    return {"zones": [{"id": z.id, "name": z.name, "r0": z.r0, "c0": z.c0, "r1": z.r1, "c1": z.c1} for z in zs]}

@router.post("/floors/{fid}/zones")
def create_zone(fid: int, data: ZoneIn, user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    z = models.Zone(floor_id=fid, name=data.name, r0=data.r0, c0=data.c0, r1=data.r1, c1=data.c1)
    db.add(z); db.commit(); db.refresh(z)
    log_op(db, user.id, "zone.create", f"楼层{fid} 创建区域 {data.name}")
    return {"zone": {"id": z.id, "name": z.name}}

@router.delete("/zones/{zid}")
def delete_zone(zid: int, user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    db.query(models.Zone).filter(models.Zone.id == zid).delete()
    db.commit()
    log_op(db, user.id, "zone.delete", f"删除区域 id={zid}")
    return {"ok": True}

class ZoneUpdate(BaseModel):
    name: str = None

@router.put("/zones/{zid}")
def update_zone(zid: int, data: ZoneUpdate, user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    z = db.query(models.Zone).filter(models.Zone.id == zid).first()
    if not z: raise HTTPException(404, "区域不存在")
    if data.name is not None: z.name = data.name[:12]
    db.commit()
    log_op(db, user.id, "zone.rename", f"区域改名 id={zid} -> {z.name}")
    return {"ok": True}
