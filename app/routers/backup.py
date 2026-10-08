import os, subprocess, tempfile, json, base64, binascii, mimetypes, zipfile
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from pydantic import BaseModel
from typing import Dict
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api", tags=["backup"])

APP_VERSION = os.getenv("APP_VERSION", "dev")
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MAX_IMG_BYTES = 15 * 1024 * 1024          # 单张图片上限（与上传一致）
MAX_ZIP_BYTES = 600 * 1024 * 1024         # 备份包上传上限
MAX_EXTRACT_TOTAL = 500 * 1024 * 1024     # 解压图片总量上限（防压缩包炸弹）


def _upload_dir():
    return os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "uploads"))


def _safe_img_rel(key):
    """图片相对路径消毒：拒绝绝对路径/目录穿越/非图片后缀，合法返回相对路径"""
    k = str(key).replace("\\", "/").strip()
    if not k or k.startswith("/") or ".." in k.split("/"):
        return None
    if not k.lower().endswith(IMG_EXTS):
        return None
    return k


def _img_path_in(img):
    """前端 img 转入库值：/uploads/x 存相对路径，http(s) 外链存原 URL，其余不入库"""
    if isinstance(img, str) and img.startswith("/uploads/"):
        return img[len("/uploads/"):]
    if isinstance(img, str) and img.startswith(("http://", "https://")):
        return img
    return None


# ============ 管理员全量备份（SQL + uploads，灾难恢复用） ============

@router.get("/backup")
def backup(user: models.User = Depends(require_role("admin")),
           db: Session = Depends(get_db)):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp = tempfile.mkdtemp()
    sql_path = os.path.join(tmp, f"wms-{stamp}.sql")
    tgz_path = os.path.join(tmp, f"wms-backup-{stamp}.tgz")
    upload_dir = _upload_dir()
    env = {
        **os.environ,
        "PGHOST": os.getenv("PGHOST", "localhost"),
        "PGPORT": os.getenv("PGPORT", "5432"),
        "PGDATABASE": os.getenv("PGDATABASE", "wms"),
        "PGUSER": os.getenv("PGUSER", "wms"),
        "PGPASSWORD": os.getenv("PGPASSWORD", "wms"),
    }
    r = subprocess.run(["pg_dump", "-F", "p", "-f", sql_path], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": "pg_dump 失败，请确认已安装 PostgreSQL 客户端"}
    r2 = subprocess.run(["tar", "-czf", tgz_path, "-C", os.path.dirname(upload_dir),
                         os.path.basename(upload_dir), "-C", tmp, os.path.basename(sql_path)],
                        capture_output=True, text=True)
    if r2.returncode != 0:
        return {"error": "打包失败"}
    log_op(db, user.id, "system.backup", f"创建备份 {stamp}")
    return FileResponse(tgz_path, filename=f"wms-backup-{stamp}.tgz")


# ============ 共享逻辑：全量导入（数据部分） ============

def _apply_import(db: Session, statuses, floors):
    """把 statuses/floors 覆盖写入第一个仓库（调用方负责 commit 与日志）。

    接受格式：{statuses: [{id, name, color, builtin}],
               floors: [{name, rows, cols, zones: [{name, r0, c0, r1, c1}],
                         slots: {"r,c": {levels: [{s, name, qty, img, note}]}}}]}
    slot key 为 "r,c"（行,列）；img 为 /uploads/... 存相对路径、http(s) 存原 URL、其它清空。
    """
    wh = db.query(models.Warehouse).order_by(models.Warehouse.id).first()
    if not wh:
        wh = models.Warehouse(name="默认仓库")
        db.add(wh); db.flush()

    # 清空旧数据：显式逐层删除子记录，不依赖数据库级联行为
    old_floor_ids = [f.id for f in db.query(models.Floor.id).filter_by(warehouse_id=wh.id).all()]
    if old_floor_ids:
        old_slot_ids = [s.id for s in db.query(models.Slot.id).filter(models.Slot.floor_id.in_(old_floor_ids)).all()]
        if old_slot_ids:
            db.query(models.Level).filter(models.Level.slot_id.in_(old_slot_ids)).delete(synchronize_session=False)
        db.query(models.Slot).filter(models.Slot.floor_id.in_(old_floor_ids)).delete(synchronize_session=False)
        db.query(models.Zone).filter(models.Zone.floor_id.in_(old_floor_ids)).delete(synchronize_session=False)
        db.query(models.SlotGroup).filter(models.SlotGroup.floor_id.in_(old_floor_ids)).delete(synchronize_session=False)
        db.query(models.Floor).filter(models.Floor.id.in_(old_floor_ids)).delete(synchronize_session=False)
    db.flush()

    # 状态：按 key 全量同步（key 是字符串标识，如 empty/full）
    if statuses:
        db.query(models.Status).delete()
        db.flush()
        for s in statuses:
            if isinstance(s, dict) and s.get("id") and s.get("name"):
                db.add(models.Status(key=s["id"], name=s["name"],
                                     color=s.get("color", "#e2e8f0"),
                                     builtin=bool(s.get("builtin"))))
        db.flush()

    for order, f in enumerate(floors or []):
        if not isinstance(f, dict):
            continue
        fl = models.Floor(warehouse_id=wh.id, name=f.get("name", "未命名楼层"),
                          rows=int(f.get("rows", 6) or 6), cols=int(f.get("cols", 10) or 10),
                          sort_order=order)
        db.add(fl); db.flush()

        for z in f.get("zones", []) or []:
            if isinstance(z, dict):
                # 前后端区域坐标同为 r0/c0/r1/c1（行/列）；兼容个别 x1/y1 旧格式
                db.add(models.Zone(floor_id=fl.id, name=z.get("name", ""),
                                   r0=int(z.get("r0", z.get("y1", 0)) or 0), c0=int(z.get("c0", z.get("x1", 0)) or 0),
                                   r1=int(z.get("r1", z.get("y2", 0)) or 0), c1=int(z.get("c1", z.get("x2", 0)) or 0)))

        created_keys = set()
        for key, st in (f.get("slots", {}) or {}).items():
            if not isinstance(st, dict):
                continue
            try:
                r, c = [int(v) for v in str(key).split(",")]  # key 是 "r,c"（行,列）
            except Exception:
                continue
            slot = models.Slot(floor_id=fl.id, r=r, c=c)
            db.add(slot); db.flush()
            for i, lv in enumerate(st.get("levels", []) or []):
                if not isinstance(lv, dict):
                    continue
                try:
                    qty = int(lv.get("qty", 0) or 0)
                except (TypeError, ValueError):
                    qty = 0
                db.add(models.Level(slot_id=slot.id, level_index=i, status_key=lv.get("s", "empty"),
                                    name=lv.get("name", "") or "", qty=qty,
                                    img_path=_img_path_in(lv.get("img")),
                                    note=str(lv.get("note", "") or "")[:500]))
            created_keys.add(f"{r},{c}")

        # 库位编组（置物架）：成员只保留本层已建的库位，一个库位只进一个组
        claimed = set()
        for gorder, g in enumerate(f.get("groups", []) or []):
            if not isinstance(g, dict):
                continue
            gname = str(g.get("name", "")).strip()[:64]
            members = []
            for mk in (g.get("members", []) or []):
                mk = str(mk)
                if mk in created_keys and mk not in claimed:
                    claimed.add(mk); members.append(mk)
            if gname and members:
                db.add(models.SlotGroup(floor_id=fl.id, name=gname,
                                        members=json.dumps(members), sort_order=gorder))


def _referenced_local_images(floors):
    """从备份 floors 数据里收集被引用的本地图片相对路径（外链不算）"""
    refs = set()
    for f in floors or []:
        if not isinstance(f, dict):
            continue
        for st in (f.get("slots", {}) or {}).values():
            if not isinstance(st, dict):
                continue
            for lv in st.get("levels", []) or []:
                if isinstance(lv, dict):
                    ip = _img_path_in(lv.get("img"))
                    if ip and not ip.startswith(("http://", "https://")):
                        refs.add(ip)
    return refs


# ============ 共享逻辑：收集一个仓库的备份数据 ============

def _collect_package(db: Session, wid: int):
    """返回 (pkg, entries, missing)：
    pkg     —— {statuses, floors}（与导入同格式，不含图片内容）
    entries —— [(相对路径, 磁盘绝对路径)]，磁盘上真实存在的本地图片（已去重）
    missing —— 数据引用了但磁盘上不存在的相对路径
    """
    upload_dir = _upload_dir()
    floors = db.query(models.Floor).filter(models.Floor.warehouse_id == wid) \
        .order_by(models.Floor.sort_order, models.Floor.id).all()
    seen, entries, missing = set(), [], []
    out_floors = []
    for fl in floors:
        zones = db.query(models.Zone).filter(models.Zone.floor_id == fl.id).order_by(models.Zone.id).all()
        slots = db.query(models.Slot).filter(models.Slot.floor_id == fl.id).all()
        groups = db.query(models.SlotGroup).filter(models.SlotGroup.floor_id == fl.id) \
            .order_by(models.SlotGroup.sort_order, models.SlotGroup.id).all()
        sids = [s.id for s in slots]
        lvs = db.query(models.Level).filter(models.Level.slot_id.in_(sids)) \
            .order_by(models.Level.slot_id, models.Level.level_index).all() if sids else []
        by_slot = {}
        for lv in lvs:
            img = None
            if lv.img_path:
                if lv.img_path.startswith(("http://", "https://")):
                    img = lv.img_path  # 外链：只存 URL，不收文件
                else:
                    img = f"/uploads/{lv.img_path}"
                    if lv.img_path not in seen:
                        seen.add(lv.img_path)
                        p = os.path.join(upload_dir, lv.img_path)
                        if os.path.isfile(p):
                            entries.append((lv.img_path, p))
                        else:
                            missing.append(lv.img_path)
            by_slot.setdefault(lv.slot_id, []).append(
                {"s": lv.status_key, "name": lv.name, "qty": lv.qty, "img": img, "note": lv.note or ""})
        out_floors.append({
            "name": fl.name, "rows": fl.rows, "cols": fl.cols, "pz": 1,
            "zones": [{"name": z.name, "r0": z.r0, "c0": z.c0, "r1": z.r1, "c1": z.c1} for z in zones],
            "slots": {f"{s.r},{s.c}": {"levels": by_slot.get(s.id, [])} for s in slots},
            "groups": [{"name": g.name, "members": json.loads(g.members or "[]")} for g in groups],
        })
    sts = db.query(models.Status).filter(
        (models.Status.warehouse_id.is_(None)) | (models.Status.warehouse_id == wid)).all()
    pkg = {
        "statuses": [{"id": s.key, "name": s.name, "color": s.color, "builtin": bool(s.builtin)} for s in sts],
        "floors": out_floors,
    }
    return pkg, entries, missing


# ============ JSON 导入（可带 base64 图片；适合小备份与旧备份） ============

class ImportIn(BaseModel):
    statuses: list = []
    floors: list = []
    images: Dict[str, str] = {}  # 本地图片相对路径 -> data URL


@router.post("/import")
def import_data(data: ImportIn,
                user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    """全量导入（JSON）：替换默认仓库的全部楼层/区域/库位，并同步状态字典。
    images 里的本地图片（base64 data URL）会写回 uploads；外链图只存 URL。
    图片很多时请用 ZIP 备份包（/api/import-archive），别用 base64 JSON。
    """
    _apply_import(db, data.statuses, data.floors)

    upload_dir = _upload_dir()
    written = skipped = 0
    for key, durl in (data.images or {}).items():
        rel = _safe_img_rel(key)
        if (rel is None or not isinstance(durl, str) or "," not in durl
                or "base64" not in durl.split(",", 1)[0]):
            skipped += 1
            continue
        try:
            raw = base64.b64decode(durl.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError):
            skipped += 1
            continue
        if not raw or len(raw) > MAX_IMG_BYTES:
            skipped += 1
            continue
        dest = os.path.join(upload_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(raw)
        written += 1

    log_op(db, user.id, "system.import", f"导入 {len(data.floors)} 个楼层，图片写回 {written} 张")
    db.commit()
    return {"ok": True, "floors": len(data.floors), "images_written": written, "images_skipped": skipped}


# ============ JSON 备份包（base64 嵌入；保留兼容，小数据可用） ============

@router.get("/warehouses/{wid}/export-package")
def export_package(wid: int, user: models.User = Depends(require_role("viewer")),
                   db: Session = Depends(get_db)):
    """JSON 备份包：与 /api/import 同格式 + 本地图片 base64 嵌入 images。
    图片多时体积大且手机解析吃力，推荐改用 ZIP 备份包 /backup-archive。"""
    w = db.query(models.Warehouse).filter(models.Warehouse.id == wid).first()
    if not w:
        raise HTTPException(404, "仓库不存在")
    pkg, entries, missing = _collect_package(db, wid)
    images = {}
    for rel, abspath in entries:
        mime = mimetypes.guess_type(abspath)[0] or "image/jpeg"
        with open(abspath, "rb") as fh:
            images[rel] = f"data:{mime};base64," + base64.b64encode(fh.read()).decode()
    log_op(db, user.id, "warehouse.export_package", f"导出 JSON 备份包：{len(images)} 张本地图")
    return {
        "format": "wms-export-package",
        "version": APP_VERSION,
        "exported_at": datetime.now().isoformat(),
        **pkg,
        "images": images,
        "images_missing": missing,
    }


# ============ ZIP 备份包（主力）：backup.json + images/ 原文件 ============

@router.get("/warehouses/{wid}/backup-archive")
def backup_archive(wid: int, user: models.User = Depends(require_role("viewer")),
                   db: Session = Depends(get_db)):
    """下载 ZIP 备份包：根目录 backup.json（数据）+ images/（本地图片原文件）。
    图片不再 base64 膨胀，服务器流式打包；外链图片只保留 URL。"""
    w = db.query(models.Warehouse).filter(models.Warehouse.id == wid).first()
    if not w:
        raise HTTPException(404, "仓库不存在")
    pkg, entries, missing = _collect_package(db, wid)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        meta = {
            "format": "wms-backup-archive",
            "version": APP_VERSION,
            "exported_at": datetime.now().isoformat(),
            "warehouse": w.name,
            "image_count": len(entries),
            "images_missing": missing,
        }
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
            data = {"format": "wms-backup-archive", "version": APP_VERSION, **pkg}
            zf.writestr("backup.json", json.dumps(data, ensure_ascii=False))
            for rel, abspath in entries:
                zf.write(abspath, arcname="images/" + rel)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    log_op(db, user.id, "warehouse.backup_archive", f"导出 ZIP 备份包：{len(entries)} 张图")
    fname = f"wms-backup-v{APP_VERSION}-{stamp}.zip"
    return FileResponse(tmp_path, filename=fname, media_type="application/zip",
                        background=BackgroundTask(os.remove, tmp_path))


@router.post("/import-archive")
async def import_archive(file: UploadFile = File(...),
                         user: models.User = Depends(require_role("operator")),
                         db: Session = Depends(get_db)):
    """恢复 ZIP 备份包：解压 backup.json 全量导入，images/ 下图片写回 uploads。
    只取 images/ 里的合法图片路径；带目录穿越/超大成员会被拒绝，防压缩包炸弹。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    total = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ZIP_BYTES:
                raise HTTPException(413, "备份包过大（上限 600MB）")
            tmp.write(chunk)
        tmp.close()
        if not zipfile.is_zipfile(tmp_path):
            raise HTTPException(400, "不是有效的 ZIP 备份包")

        upload_dir = _upload_dir()
        written = skipped = extracted = 0
        with zipfile.ZipFile(tmp_path) as zf:
            pairs = [(n.replace("\\", "/"), n) for n in zf.namelist()]
            data_name = next((o for norm, o in pairs if norm == "backup.json"), None) or \
                next((o for norm, o in pairs if norm.endswith("/backup.json")), None)
            if not data_name:
                raise HTTPException(400, "压缩包里没找到 backup.json")
            info = zf.getinfo(data_name)
            if info.file_size > 30 * 1024 * 1024:
                raise HTTPException(400, "backup.json 过大")
            try:
                pkg = json.loads(zf.read(data_name).decode("utf-8"))
            except Exception:
                raise HTTPException(400, "backup.json 解析失败")
            floors = pkg.get("floors") if isinstance(pkg, dict) else None
            if not isinstance(floors, list):
                raise HTTPException(400, "备份数据格式不对（缺 floors）")

            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or not name.startswith("images/"):
                    continue
                rel = _safe_img_rel(name[len("images/"):])
                if rel is None or info.file_size > MAX_IMG_BYTES or extracted + info.file_size > MAX_EXTRACT_TOTAL:
                    skipped += 1
                    continue
                dest = os.path.join(upload_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                got = 0
                ok = True
                with zf.open(info) as src, open(dest, "wb") as out:
                    while True:
                        b = src.read(262144)
                        if not b:
                            break
                        got += len(b)
                        if got > MAX_IMG_BYTES:
                            ok = False
                            break
                        out.write(b)
                if not ok:
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    skipped += 1
                    continue
                extracted += got
                written += 1
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    _apply_import(db, pkg.get("statuses") or [], floors)
    refs = _referenced_local_images(floors)
    missing_n = sum(1 for rel in refs if not os.path.isfile(os.path.join(upload_dir, rel)))
    log_op(db, user.id, "system.import_archive",
           f"ZIP 恢复 {len(floors)} 个楼层，图片 {written} 张")
    db.commit()
    return {"ok": True, "floors": len(floors), "images_written": written,
            "images_skipped": skipped, "images_missing": missing_n}
