"""货物目录：预存货物名称 + 树形品类（例：自行车系列 > 刹车零件）。
录入时从目录选用，避免同一货物多种叫法。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..db import get_db
from .. import models
from ..auth import require_role, log_op

router = APIRouter(prefix="/api/goods", tags=["goods"])


def _descendants(db: Session, cid: int):
    """收集 cid 及其全部子孙品类 id（含自己）"""
    out, stack = set(), [cid]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        for (kid,) in db.query(models.GoodsCategory.id)\
                        .filter(models.GoodsCategory.parent_id == cur).all():
            stack.append(kid)
    return out


def _build_tree(cats):
    """按 sort_order 排好后拼成嵌套树"""
    by_parent = {}
    for c in cats:
        by_parent.setdefault(c.parent_id, []).append(c)
    for v in by_parent.values():
        v.sort(key=lambda x: (x.sort_order, x.id))

    def node(c):
        return {"id": c.id, "name": c.name, "parent_id": c.parent_id,
                "children": [node(x) for x in by_parent.get(c.id, [])]}
    return [node(c) for c in by_parent.get(None, [])]


def _cat_name(db, cid):
    """品类 id 转显示名（None=顶层）"""
    if cid is None:
        return "顶层"
    c = db.query(models.GoodsCategory).filter(models.GoodsCategory.id == cid).first()
    return c.name if c else "？"


def _cat_out(c):
    return {"id": c.id, "name": c.name, "parent_id": c.parent_id, "sort_order": c.sort_order}


def _item_out(db, it):
    cname = None
    if it.category_id:
        c = db.query(models.GoodsCategory).filter(models.GoodsCategory.id == it.category_id).first()
        cname = c.name if c else None
    return {"id": it.id, "name": it.name, "category_id": it.category_id,
            "category_name": cname, "note": it.note or "", "sort_order": it.sort_order}


@router.get("/catalog")
def get_catalog(user: models.User = Depends(require_role("viewer")),
                db: Session = Depends(get_db)):
    """目录整体：品类树（每个节点带 items）+ 未分类货物"""
    cats = db.query(models.GoodsCategory)\
             .order_by(models.GoodsCategory.sort_order, models.GoodsCategory.id).all()
    items = db.query(models.GoodsItem)\
              .order_by(models.GoodsItem.sort_order, models.GoodsItem.id).all()
    by_cat = {}
    for it in items:
        by_cat.setdefault(it.category_id, []).append(it)
    tree = _build_tree(cats)

    def attach(nodes):
        for n in nodes:
            n["items"] = [_item_out(db, it) for it in by_cat.get(n["id"], [])]
            attach(n["children"])
    attach(tree)
    return {"tree": tree,
            "uncategorized": [_item_out(db, it) for it in by_cat.get(None, [])]}


class CategoryIn(BaseModel):
    name: str
    parent_id: Optional[int] = None
    sort_order: int = 0


class CategoryUpd(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None


@router.post("/categories")
def create_category(data: CategoryIn, user: models.User = Depends(require_role("operator")),
                    db: Session = Depends(get_db)):
    name = data.name.strip()[:64]
    if not name:
        raise HTTPException(400, "品类名称不能为空")
    if data.parent_id is not None and not db.query(models.GoodsCategory)\
            .filter(models.GoodsCategory.id == data.parent_id).first():
        raise HTTPException(400, "父品类不存在")
    c = models.GoodsCategory(name=name, parent_id=data.parent_id, sort_order=data.sort_order or 0)
    db.add(c); db.commit(); db.refresh(c)
    log_op(db, user.id, "goods.category.create", f"新增品类 {name}(id={c.id})")
    return {"category": _cat_out(c)}


@router.put("/categories/{cid}")
def update_category(cid: int, data: CategoryUpd,
                    user: models.User = Depends(require_role("operator")),
                    db: Session = Depends(get_db)):
    c = db.query(models.GoodsCategory).filter(models.GoodsCategory.id == cid).first()
    if not c:
        raise HTTPException(404, "品类不存在")
    changes = []
    if data.name is not None:
        name = data.name.strip()[:64]
        if not name:
            raise HTTPException(400, "品类名称不能为空")
        if name != c.name:
            changes.append(f"名称「{c.name}」→「{name}」")
        c.name = name
    if data.sort_order is not None:
        c.sort_order = data.sort_order
    if data.parent_id is not None:
        if data.parent_id == cid:
            raise HTTPException(400, "不能把自己设为自己的父品类")
        if data.parent_id < 0:
            new_parent = None  # 传 -1 表示移到顶层
        else:
            p = db.query(models.GoodsCategory).filter(models.GoodsCategory.id == data.parent_id).first()
            if not p:
                raise HTTPException(400, "父品类不存在")
            if data.parent_id in _descendants(db, cid):
                raise HTTPException(400, "不能把品类移到自己的子孙下面")
            new_parent = data.parent_id
        if new_parent != c.parent_id:
            changes.append(f"父品类「{_cat_name(db, c.parent_id)}」→「{_cat_name(db, new_parent)}」")
        c.parent_id = new_parent
    db.commit()
    log_op(db, user.id, "goods.category.update",
           f"修改品类「{c.name}」(id={cid})" + (f"：{'；'.join(changes)}" if changes else "（无实质改动）"))
    return {"category": _cat_out(c)}


@router.delete("/categories/{cid}")
def delete_category(cid: int, user: models.User = Depends(require_role("operator")),
                    db: Session = Depends(get_db)):
    c = db.query(models.GoodsCategory).filter(models.GoodsCategory.id == cid).first()
    if not c:
        raise HTTPException(404, "品类不存在")
    ids = _descendants(db, cid)
    n_cats = len(ids)
    cat_name = c.name
    n_items = db.query(models.GoodsItem)\
                .filter(models.GoodsItem.category_id.in_(ids))\
                .update({"category_id": None}, synchronize_session=False)
    db.query(models.GoodsCategory).filter(models.GoodsCategory.id.in_(ids))\
      .delete(synchronize_session=False)
    db.commit()
    log_op(db, user.id, "goods.category.delete",
           f"删除品类「{cat_name}」(id={cid}，含 {n_cats - 1} 个子品类），{n_items} 件货物移入未分类")
    return {"ok": True}


class ItemIn(BaseModel):
    name: str
    category_id: Optional[int] = None
    note: str = ""
    sort_order: int = 0


class ItemUpd(BaseModel):
    name: Optional[str] = None
    category_id: Optional[int] = None
    note: Optional[str] = None
    sort_order: Optional[int] = None


@router.post("/items")
def create_item(data: ItemIn, user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    name = data.name.strip()[:64]
    if not name:
        raise HTTPException(400, "货物名称不能为空")
    if data.category_id is not None and not db.query(models.GoodsCategory)\
            .filter(models.GoodsCategory.id == data.category_id).first():
        raise HTTPException(400, "品类不存在")
    it = models.GoodsItem(name=name, category_id=data.category_id,
                          note=(data.note or "")[:255], sort_order=data.sort_order or 0)
    db.add(it); db.commit(); db.refresh(it)
    log_op(db, user.id, "goods.item.create", f"新增货物 {name}(id={it.id})")
    return {"item": _item_out(db, it)}


@router.put("/items/{iid}")
def update_item(iid: int, data: ItemUpd,
                user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    it = db.query(models.GoodsItem).filter(models.GoodsItem.id == iid).first()
    if not it:
        raise HTTPException(404, "货物不存在")
    changes = []
    renamed_levels = 0
    if data.name is not None:
        name = data.name.strip()[:64]
        if not name:
            raise HTTPException(400, "货物名称不能为空")
        if name != it.name:
            changes.append(f"货名「{it.name}」→「{name}」")
            # 改名级联：库位层级里与旧名完全相同的，一并改成新名
            renamed_levels = db.query(models.Level)\
                .filter(models.Level.name == it.name)\
                .update({"name": name}, synchronize_session=False)
            it.name = name
    if data.note is not None:
        new_note = (data.note or "")[:255]
        if new_note != (it.note or ""):
            changes.append("备注已修改")
        it.note = new_note
    if data.sort_order is not None:
        it.sort_order = data.sort_order
    if data.category_id is not None:
        if data.category_id < 0:
            new_cid = None  # 传 -1 表示移入未分类
        else:
            if not db.query(models.GoodsCategory)\
                    .filter(models.GoodsCategory.id == data.category_id).first():
                raise HTTPException(400, "品类不存在")
            new_cid = data.category_id
        if new_cid != it.category_id:
            changes.append(f"品类「{_cat_name(db, it.category_id)}」→「{_cat_name(db, new_cid)}」")
        it.category_id = new_cid
    db.commit()
    detail = f"修改货物「{it.name}」(id={iid})" + (f"：{'；'.join(changes)}" if changes else "（无实质改动）")
    if renamed_levels:
        detail += f"，改名同步 {renamed_levels} 个库位层级"
    log_op(db, user.id, "goods.item.update", detail)
    out = _item_out(db, it)
    out["renamed_levels"] = renamed_levels
    return {"item": out}


@router.delete("/items/{iid}")
def delete_item(iid: int, user: models.User = Depends(require_role("operator")),
                db: Session = Depends(get_db)):
    it = db.query(models.GoodsItem).filter(models.GoodsItem.id == iid).first()
    if not it:
        raise HTTPException(404, "货物不存在")
    item_name = it.name
    db.delete(it); db.commit()
    log_op(db, user.id, "goods.item.delete", f"删除货物「{item_name}」(id={iid})")
    return {"ok": True}


@router.get("/items")
def list_items(user: models.User = Depends(require_role("viewer")),
               db: Session = Depends(get_db),
               keyword: str = Query("", max_length=64),
               category_id: int = None,
               limit: int = Query(200, ge=1, le=1000),
               offset: int = Query(0, ge=0)):
    """按关键词/品类查货物（分页上限 1000）"""
    qry = db.query(models.GoodsItem)
    if keyword:
        qry = qry.filter(models.GoodsItem.name.like(f"%{keyword}%"))
    if category_id is not None:
        qry = qry.filter(models.GoodsItem.category_id == category_id)
    total = qry.count()
    rows = qry.order_by(models.GoodsItem.sort_order, models.GoodsItem.id)\
              .offset(offset).limit(limit).all()
    return {"total": total, "items": [_item_out(db, it) for it in rows]}
