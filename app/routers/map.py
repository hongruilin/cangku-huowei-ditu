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
    groups: list = None  # 出现 groups 字段时全量替换该楼层编组；不出现则保留原有编组

def _code(r: int, c: int) -> str:
    return chr(65 + r) + "-" + str(c + 1).zfill(2)

def _status_name(db: Session, wid, key: str) -> str:
    """状态 key 转显示名（先查仓库自定义，再查内置）"""
    if wid is not None:
        s = db.query(models.Status).filter(models.Status.warehouse_id == wid,
                                           models.Status.key == key).first()
        if s: return s.name
    s = db.query(models.Status).filter(models.Status.warehouse_id.is_(None),
                                      models.Status.key == key).first()
    return s.name if s else key


def _level_diff(db: Session, wid, code: str, idx: int, o: dict, n: dict):
    """单个层级旧→新差异，返回描述片段列表"""
    fds = []
    if (o["name"] or "") != (n["name"] or ""):
        fds.append(f"货名「{o['name'] or '（空）'}」→「{n['name'] or '（空）'}」")
    if (o["qty"] or 0) != (n["qty"] or 0):
        fds.append(f"数量 {o['qty']}→{n['qty']}")
    if o["status_key"] != n["status_key"]:
        fds.append(f"状态 {_status_name(db, wid, o['status_key'])}→{_status_name(db, wid, n['status_key'])}")
    if (o["note"] or "") != (n["note"] or ""):
        fds.append("备注已修改")
    if bool(o["img_path"]) != bool(n["img_path"]):
        fds.append("照片已更新" if n["img_path"] else "照片已清除")
    if not fds:
        return None
    return f"{code} 第{idx + 1}层" + "、".join(fds)


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
    import json as _json
    groups = db.query(models.SlotGroup).filter(models.SlotGroup.floor_id == fid)\
        .order_by(models.SlotGroup.sort_order, models.SlotGroup.id).all()
    return {
        "floor": {"id": f.id, "name": f.name, "rows": f.rows, "cols": f.cols},
        "slots": out,
        "zones": [{"id": str(z.id), "name": z.name, "r0": z.r0, "c0": z.c0, "r1": z.r1, "c1": z.c1}
                  for z in zones],
        "groups": [{"id": str(g.id), "name": g.name,
                    "members": _json.loads(g.members or "[]")} for g in groups],
    }

@router.put("/floors/{fid}/map")
def save_map(fid: int, data: MapSave, user: models.User = Depends(require_role("operator")),
             db: Session = Depends(get_db)):
    f = db.query(models.Floor).filter(models.Floor.id == fid).first()
    if not f: raise HTTPException(404, "楼层不存在")
    wid = f.warehouse_id
    # 先快照旧层级，供保存后做改动 diff（日志用）
    old_levels = {}
    old_slots = db.query(models.Slot).filter(models.Slot.floor_id == fid).all()
    for s in old_slots:
        for lv in db.query(models.Level).filter(models.Level.slot_id == s.id).all():
            old_levels[(s.r, s.c, lv.level_index)] = {
                "status_key": lv.status_key, "name": lv.name or "", "qty": lv.qty or 0,
                "note": lv.note or "", "img_path": lv.img_path}
    old_n_group = db.query(models.SlotGroup).filter(models.SlotGroup.floor_id == fid).count()
    # 清旧库位：先显式删层级，再删库位（不依赖数据库级联行为）
    old_slot_ids = [s.id for s in old_slots]
    if old_slot_ids:
        db.query(models.Level).filter(models.Level.slot_id.in_(old_slot_ids)).delete(synchronize_session=False)
    db.query(models.Slot).filter(models.Slot.floor_id == fid).delete()
    db.flush()
    n_slot = n_level = 0
    saved_keys = set()
    new_levels = {}
    for key, st in data.slots.items():
        try:
            rr, cc = key.split(","); r, c = int(rr), int(cc)
        except ValueError:
            continue
        saved_keys.add(f"{r},{c}")
        slot = models.Slot(floor_id=fid, r=r, c=c)
        db.add(slot); db.flush()
        n_slot += 1
        for i, lv in enumerate(st.get("levels", []) if isinstance(st, dict) else []):
            img_path = _img_in(lv.get("img") if isinstance(lv, dict) else None)
            qty = max(0, int(lv.get("qty", 0) or 0)) if isinstance(lv, dict) else 0
            sk = str(lv.get("s", "empty"))[:32] if isinstance(lv, dict) else "empty"
            if qty <= 0:
                sk = "empty"  # 数量为 0 自动置空状态
            # 注意：qty 从 0 改为正数时不自动翻有货——整图保存分不清"用户手动设空"还是"没动过"，
            # 自动翻会覆盖用户的手动选择；0→正数的自动翻转由前端 applyQtyStatus 和 MCP 单字段更新负责
            norm = {"status_key": sk,
                    "name": str(lv.get("name", ""))[:64] if isinstance(lv, dict) else "",
                    "qty": qty, "img_path": img_path,
                    "note": str(lv.get("note", ""))[:500] if isinstance(lv, dict) else ""}
            new_levels[(r, c, i)] = norm
            db.add(models.Level(slot_id=slot.id, level_index=i, **norm))
            n_level += 1
    n_group = 0
    if data.groups is not None:
        # 带 groups 字段保存：全量替换该楼层编组；成员须是已保存的库位，一个库位只进一个组
        import json as _json
        db.query(models.SlotGroup).filter(models.SlotGroup.floor_id == fid).delete()
        claimed = set()
        for order, g in enumerate(data.groups if isinstance(data.groups, list) else []):
            if not isinstance(g, dict):
                continue
            name = str(g.get("name", "")).strip()[:64]
            members = []
            for mk in g.get("members", []) if isinstance(g.get("members"), list) else []:
                mk = str(mk)
                if mk in saved_keys and mk not in claimed:
                    claimed.add(mk); members.append(mk)
            if not name or not members:
                continue
            db.add(models.SlotGroup(floor_id=fid, name=name, members=_json.dumps(members),
                                    sort_order=order))
            n_group += 1
    # 新旧 diff：只记真正的改动
    changes = []
    old_slot_keys = {(r, c) for (r, c, _i) in old_levels}
    new_slot_keys = {(r, c) for (r, c, _i) in new_levels}
    for (r, c) in sorted(new_slot_keys - old_slot_keys):
        changes.append(f"新增库位 {_code(r, c)}")
    for (r, c) in sorted(old_slot_keys - new_slot_keys):
        changes.append(f"删除库位 {_code(r, c)}")
    for key in sorted(set(old_levels) & set(new_levels)):
        r, c, i = key
        d = _level_diff(db, wid, _code(r, c), i, old_levels[key], new_levels[key])
        if d:
            changes.append(d)
    if n_group != old_n_group:
        changes.append(f"编组 {old_n_group}→{n_group}")
    db.commit()
    if changes:
        detail = f"保存楼层「{f.name}」地图：" + "；".join(changes[:12])
        if len(changes) > 12:
            detail += f"（等共 {len(changes)} 处改动）"
        log_op(db, user.id, "map.save", detail)
    return {"ok": True, "slots": n_slot, "levels": n_level, "groups": n_group}

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
        out_floors.append({"floor": m["floor"], "slots": m["slots"], "zones": m["zones"], "groups": m["groups"]})
    sts = db.query(models.Status).filter(
        (models.Status.warehouse_id.is_(None)) | (models.Status.warehouse_id == wid)).all()
    log_op(db, user.id, "warehouse.export", f"导出仓库{wid}数据")
    return {
        "warehouse": {"id": w.id, "name": w.name},
        "floors": out_floors,
        "statuses": [{"key": s.key, "name": s.name, "color": s.color, "builtin": s.builtin} for s in sts],
        "exported_at": datetime.now().isoformat(),
    }
