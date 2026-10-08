from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from sqlalchemy.orm import Session
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api", tags=["map"])

class MapSave(BaseModel):
    slots: Dict[str, Any]

def _code(r: int, c: int) -> str:
    return chr(65 + r) + "-" + str(c + 1).zfill(2)

def _img_out(img_path):
    """库里存的 img_path 转前端可用地址：外链原样返回，本地相对路径拼 /uploads/"""
    if not img_path:
        return None
    if img_path.startswith(("http://", "https://")):
        return img_path
    return f"/uploads/{img_path}"

def _img_in(img):
    """前端 img 转入库值：/uploads/xxx 存相对路径，外链存原 URL，其它（idb:/data:/blob:）不入库"""
    if not isinstance(img, str):
        return None
    if img.startswith("/uploads/"):
        return img[len("/uploads/"):]
    if img.startswith(("http://", "https://")):
        return img
    return None

@router.get("/floors/{fid}/map")
def get_map(fid: int, user: models.User = Depends(require_role("viewer")),
            db: Session = Depends(get_db)):
    f = db.query(models.Floor).filter(models.Floor.id == fid).first()
    if not f: raise HTTPException(404, "楼层不存在")
    slots = db.query(models.Slot).filter(models.Slot.floor_id == fid).all()
    slot_ids = [s.id for s in slots]
    levels = []
    if slot_ids:
        levels = db.query(models.Level).filter(models.Level.slot_id.in_(slot_ids))\
                     .order_by(models.Level.slot_id, models.Level.level_index).all()
    by_slot = {}
    for lv in levels:
        by_slot.setdefault(lv.slot_id, {})[lv.level_index] = {
            "s": lv.status_key, "name": lv.name, "qty": lv.qty,
            "img": _img_out(lv.img_path),
            "note": lv.note or "",
        }
    out = {}
    for s in slots:
        lv_map = by_slot.get(s.id, {})
        max_idx = max(lv_map.keys()) if lv_map else -1
        out[f"{s.r},{s.c}"] = {
            "levels": [lv_map.get(i, {"s": "empty", "name": "", "qty": 0, "img": None, "note": ""})
                       for i in range(max_idx + 1)]
        }
    zones = db.query(models.Zone).filter(models.Zone.floor_id == fid).order_by(models.Zone.id).all()
    return {
        "floor": {"id": f.id, "name": f.name, "rows": f.rows, "cols": f.cols},
        "slots": out,
        "zones": [{"id": str(z.id), "name": z.name, "r0": z.r0, "c0": z.c0, "r1": z.r1, "c1": z.c1}
                  for z in zones],
    }

@router.put("/floors/{fid}/map")
def save_map(fid: int, data: MapSave, user: models.User = Depends(require_role("operator")),
             db: Session = Depends(get_db)):
    f = db.query(models.Floor).filter(models.Floor.id == fid).first()
    if not f: raise HTTPException(404, "楼层不存在")
    # 清旧库位：先显式删层级，再删库位（不依赖数据库级联行为）
    old_slot_ids = [s.id for s in db.query(models.Slot.id).filter(models.Slot.floor_id == fid).all()]
    if old_slot_ids:
        db.query(models.Level).filter(models.Level.slot_id.in_(old_slot_ids)).delete(synchronize_session=False)
    db.query(models.Slot).filter(models.Slot.floor_id == fid).delete()
    db.flush()
    n_slot = n_level = 0
    for key, st in data.slots.items():
        try:
            rr, cc = key.split(","); r, c = int(rr), int(cc)
        except ValueError:
            continue
        slot = models.Slot(floor_id=fid, r=r, c=c)
        db.add(slot); db.flush()
        n_slot += 1
        for i, lv in enumerate(st.get("levels", []) if isinstance(st, dict) else []):
            img_path = _img_in(lv.get("img") if isinstance(lv, dict) else None)
            db.add(models.Level(
                slot_id=slot.id, level_index=i,
                status_key=str(lv.get("s", "empty"))[:32] if isinstance(lv, dict) else "empty",
                name=str(lv.get("name", ""))[:64] if isinstance(lv, dict) else "",
                qty=max(0, int(lv.get("qty", 0) or 0)) if isinstance(lv, dict) else 0,
                img_path=img_path,
                note=str(lv.get("note", ""))[:500] if isinstance(lv, dict) else "",
            ))
            n_level += 1
    db.commit()
    log_op(db, user.id, "map.save", f"保存楼层{fid}地图：{n_slot}库位/{n_level}层级")
    return {"ok": True, "slots": n_slot, "levels": n_level}

@router.get("/warehouses/{wid}/export")
def export_warehouse(wid: int, user: models.User = Depends(require_role("viewer")),
                     db: Session = Depends(get_db)):
    from datetime import datetime
    w = db.query(models.Warehouse).filter(models.Warehouse.id == wid).first()
    if not w: raise HTTPException(404, "仓库不存在")
    floors = db.query(models.Floor).filter(models.Floor.warehouse_id == wid)\
                 .order_by(models.Floor.sort_order, models.Floor.id).all()
    out_floors = []
    for fl in floors:
        # 复用 get_map 逻辑
        m = get_map(fl.id, user, db)
        out_floors.append({"floor": m["floor"], "slots": m["slots"], "zones": m["zones"]})
    sts = db.query(models.Status).filter(
        (models.Status.warehouse_id.is_(None)) | (models.Status.warehouse_id == wid)).all()
    log_op(db, user.id, "warehouse.export", f"导出仓库{wid}数据")
    return {
        "warehouse": {"id": w.id, "name": w.name},
        "floors": out_floors,
        "statuses": [{"key": s.key, "name": s.name, "color": s.color, "builtin": s.builtin} for s in sts],
        "exported_at": datetime.now().isoformat(),
    }
