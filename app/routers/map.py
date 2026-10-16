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
    # 旧编组明细（日志 diff 用）：[(名称, 排序后成员"r,c"元组)]
    import json as _json2
    old_groups = []
    for g in db.query(models.SlotGroup).filter(models.SlotGroup.floor_id == fid)\
            .order_by(models.SlotGroup.sort_order, models.SlotGroup.id).all():
        try:
            _members = tuple(sorted(str(m) for m in (_json2.loads(g.members or "[]") or [])))
        except Exception:
            _members = ()
        old_groups.append((g.name or "", _members))
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
    new_groups = []  # [(名称, 排序后成员"r,c"元组)]，日志 diff 用
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
            new_groups.append((name, tuple(sorted(members))))
    elif data.groups is None:
        # 没带 groups 字段：编组原样保留，不算改动
        n_group = old_n_group
        new_groups = list(old_groups)
    # 新旧 diff：只记真正的改动
    changes = []
    # 库位内容签名：每层 (序号, 状态, 货名, 数量, 备注, 有无图片)
    def _slot_sig(levels_dict, r, c):
        idxs = sorted({k[2] for k in levels_dict if k[0] == r and k[1] == c})
        sig = []
        for i in idxs:
            lv = levels_dict[(r, c, i)]
            sig.append((i, lv.get("status_key"), lv.get("name") or "",
                        lv.get("qty") or 0, lv.get("note") or "",
                        bool(lv.get("img_path"))))
        return tuple(sig)
    old_sigs = {(s.r, s.c): _slot_sig(old_levels, s.r, s.c) for s in old_slots}
    new_sigs = {}
    for _key in saved_keys:
        try:
            _rr, _cc = _key.split(","); _r, _c = int(_rr), int(_cc)
        except ValueError:
            continue
        new_sigs[(_r, _c)] = _slot_sig(new_levels, _r, _c)
    _deleted = sorted(set(old_sigs) - set(new_sigs))
    _added = sorted(set(new_sigs) - set(old_sigs))
    # 1) 移动：删掉的位置与新增的位置内容完全一致
    _moves = []  # [(from_r,from_c),(to_r,to_c)]
    _rem_del, _rem_add = list(_deleted), list(_added)
    for _d in _deleted:
        for _a in list(_rem_add):
            if old_sigs[_d] == new_sigs[_a]:
                _moves.append((_d, _a))
                _rem_add.remove(_a); _rem_del.remove(_d)
                break
    for (_r1, _c1), (_r2, _c2) in _moves:
        changes.append(f"移动库位 {_code(_r1, _c1)}→{_code(_r2, _c2)}")
    # 2) 复制：新增位置内容与某个原位未动的库位完全一致
    for _a in list(_rem_add):
        for _pos in sorted(set(old_sigs) & set(new_sigs)):
            if new_sigs[_a] == old_sigs[_pos] and old_sigs[_pos] == new_sigs[_pos]:
                changes.append(f"复制库位 {_code(*_pos)}→{_code(*_a)}")
                _rem_add.remove(_a)
                break
    for (_r, _c) in _rem_add:
        changes.append(f"新增库位 {_code(_r, _c)}")
    for (_r, _c) in _rem_del:
        changes.append(f"删除库位 {_code(_r, _c)}")
    for key in sorted(set(old_levels) & set(new_levels)):
        r, c, i = key
        d = _level_diff(db, wid, _code(r, c), i, old_levels[key], new_levels[key])
        if d:
            changes.append(d)
    # 3) 编组 diff：成员位置按库位移动做换算，避免"移动库位"连带误报"成员变更"
    _move_map = {f"{_r1},{_c1}": f"{_r2},{_c2}" for (_r1, _c1), (_r2, _c2) in _moves}
    def _trans(members):
        return tuple(sorted(_move_map.get(m, m) for m in members))
    _og = [(_n, _trans(_m)) for _n, _m in old_groups]
    _ng = list(new_groups)
    _used_o, _used_n = set(), set()
    # 先按成员配对：成员一致 → 改名或无变化
    for _oi, (_on, _om) in enumerate(_og):
        for _ni, (_nn, _nm) in enumerate(_ng):
            if _ni in _used_n or _om != _nm:
                continue
            _used_o.add(_oi); _used_n.add(_ni)
            if _on != _nn:
                changes.append(f"编组「{_on}」改名「{_nn}」")
            break
    # 再按名称配对：名称一致 → 成员变化
    for _oi, (_on, _om) in enumerate(_og):
        if _oi in _used_o:
            continue
        for _ni, (_nn, _nm) in enumerate(_ng):
            if _ni in _used_n or _on != _nn:
                continue
            _used_o.add(_oi); _used_n.add(_ni)
            changes.append(f"编组「{_nn}」成员 {len(_om)}→{len(_nm)}个库位")
            break
    for _oi, (_on, _om) in enumerate(_og):
        if _oi not in _used_o:
            changes.append(f"删除编组「{_on}」")
    for _ni, (_nn, _nm) in enumerate(_ng):
        if _ni not in _used_n:
            changes.append(f"新建编组「{_nn}」({len(_nm)}个库位)")
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
