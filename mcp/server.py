# WMS MCP Server (Python)：供 AI 增删改查仓库数据
# 运行：python mcp/server.py （stdio 模式）
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wms:wms@localhost:5432/wms")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads"))
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

server = Server("wms-mcp", instructions=(
    "这是一个仓库货位地图（WMS）的 AI 接口，有多楼层，仓库里许多库位（A-01 这样的编号）被组合成具名的置物架（即编组，如「1号货架」），每个库位有多层。\n"
    "用户问东西在哪时：先用 wms_search_goods 按货物名搜索；搜不到时用 wms_view_images 按楼层逐层看照片比对找物，找到候选再用 wms_view_image 细看确认。\n"
    "工具结果带 location 字段，已拼成人话位置（如「1F · 1号货架第2个库位第3层（A-03）」，组内库位顺序为先从上往下排、同一行从左往右数」）。回答位置时必须直接引用 location：先说货架名、组内第几个库位和第几层，库位编码只放在括号里作对照；不要只报 A-03 这类编码。只有该库位确实没有编组时，才用「楼层 + 编码」表达。\n"
    "修改库存、移动库位、改编组等写操作前，先用 wms_get_slot / wms_get_floor_map 核对目标格再执行。"
))

TOOLS = [
    Tool(name="wms_list_warehouses", description="列出所有仓库",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="wms_list_floors", description="列出指定仓库的楼层",
         inputSchema={"type": "object", "properties": {
             "warehouse_id": {"type": "number"}}, "required": ["warehouse_id"]}),
    Tool(name="wms_get_floor_map", description="获取某楼层完整地图（库位、层级、货物、区域）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"}}, "required": ["floor_id"]}),
    Tool(name="wms_search_goods", description="按货物名称搜索库位",
         inputSchema={"type": "object", "properties": {
             "keyword": {"type": "string"},
             "warehouse_id": {"type": "number"}}, "required": ["keyword"]}),
    Tool(name="wms_get_slot", description="获取指定库位详情",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "code": {"type": "string", "description": "如 B-04"}}, "required": ["floor_id", "code"]}),
    Tool(name="wms_update_level", description="更新库位某层货物（名称、数量、状态、备注）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"}, "code": {"type": "string"},
             "level_index": {"type": "number"},
             "name": {"type": "string"}, "qty": {"type": "number"},
             "status_key": {"type": "string"},
             "note": {"type": "string", "description": "层备注（可选）"},
             "img": {"type": "string", "description": "图片（可选）：/uploads/... 本地路径、http(s) 外链，或空字符串清除"}}, "required": ["floor_id", "code", "level_index"]}),
    Tool(name="wms_move_slot", description="把整个库位移动到同楼层另一个格子（所有层、照片、备注一起搬，源格清空）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "from_code": {"type": "string", "description": "源库位，如 B-04"},
             "to_code": {"type": "string", "description": "目标库位，如 C-07"},
             "overwrite": {"type": "boolean", "description": "目标已有库位时是否覆盖，默认 false"}}, "required": ["floor_id", "from_code", "to_code"]}),
    Tool(name="wms_copy_slot", description="把整个库位复制到同楼层另一个格子（源格保留，层、照片、备注一起复制）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "from_code": {"type": "string", "description": "源库位，如 B-04"},
             "to_code": {"type": "string", "description": "目标库位，如 C-07"},
             "overwrite": {"type": "boolean", "description": "目标已有库位时是否覆盖，默认 false"}}, "required": ["floor_id", "from_code", "to_code"]}),
    Tool(name="wms_move_slots", description="批量移动多个库位（items 给源→目标成对映射，一个事务完成，支持 A→B、B→C 连环）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "items": {"type": "array", "description": "如 [{\"from_code\":\"B-02\",\"to_code\":\"C-03\"}]",
                       "items": {"type": "object", "properties": {
                           "from_code": {"type": "string"}, "to_code": {"type": "string"}},
                           "required": ["from_code", "to_code"]}},
             "overwrite": {"type": "boolean", "description": "目标被非源库位占用时是否覆盖，默认 false"}}, "required": ["floor_id", "items"]}),
    Tool(name="wms_copy_slots", description="批量复制多个库位（items 给源→目标成对映射，源格保留，一个事务完成）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "items": {"type": "array", "description": "如 [{\"from_code\":\"B-02\",\"to_code\":\"C-03\"}]",
                       "items": {"type": "object", "properties": {
                           "from_code": {"type": "string"}, "to_code": {"type": "string"}},
                           "required": ["from_code", "to_code"]}},
             "overwrite": {"type": "boolean", "description": "目标已有库位时是否覆盖，默认 false"}}, "required": ["floor_id", "items"]}),
    Tool(name="wms_inventory_summary", description="库存汇总",
         inputSchema={"type": "object", "properties": {
             "warehouse_id": {"type": "number"}}}),
    Tool(name="wms_list_zones", description="列出某楼层区域",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"}}, "required": ["floor_id"]}),
    Tool(name="wms_view_image", description="查看某个库位某一层的货物照片（返回图片本体，AI 能直接看见内容，用于确认实物）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "code": {"type": "string", "description": "如 B-04"},
             "level_index": {"type": "number", "description": "层序号，从 0 开始"}}, "required": ["floor_id", "code", "level_index"]}),
    Tool(name="wms_view_images", description="批量查看某楼层的货物照片（缩略图，每张标注所在库位/层/货名）。用户发图问东西在仓库哪里时，用它逐层扫图比对找位置",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "code": {"type": "string", "description": "只看某个库位（可选）"},
             "keyword": {"type": "string", "description": "按货名关键词过滤（可选）"},
             "limit": {"type": "number", "description": "这次最多返回几张，默认 6，上限 12"},
             "offset": {"type": "number", "description": "从第几张开始（翻页用），默认 0"}}, "required": ["floor_id"]}),
    Tool(name="wms_list_groups", description="列出某楼层的库位编组（置物架）：组名和包含的库位",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"}}, "required": ["floor_id"]}),
    Tool(name="wms_create_group", description="把多个库位组成一个置物架并命名（如把 A-01..A-04 组成「1号架」）。一个库位只能属于一个编组",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "name": {"type": "string", "description": "编组名称，如 1号架"},
             "codes": {"type": "array", "items": {"type": "string"}, "description": "库位编码列表，如 [\"A-01\",\"A-02\"]"}}, "required": ["floor_id", "name", "codes"]}),
    Tool(name="wms_update_group", description="修改编组：改名（name）和/或整体替换成员（codes）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "group_id": {"type": "number"},
             "name": {"type": "string"},
             "codes": {"type": "array", "items": {"type": "string"}}}, "required": ["floor_id", "group_id"]}),
    Tool(name="wms_delete_group", description="解散编组（只删编组本身，库位和货物不受影响）",
         inputSchema={"type": "object", "properties": {
             "floor_id": {"type": "number"},
             "group_id": {"type": "number"}}, "required": ["floor_id", "group_id"]}),
]

def code_to_rc(code: str):
    import re
    m = re.match(r"^([A-Z])-(\d+)$", code.upper())
    if not m: return None
    return ord(m.group(1)) - 65, int(m.group(2)) - 1

def q(sql, params=None):
    db = Session()
    try:
        rows = db.execute(text(sql), params or {}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()

def q_one(sql, params=None):
    r = q(sql, params)
    return r[0] if r else None

def _img_out(p):
    """库里 img_path 转可用地址：外链原样，本地相对路径拼 /uploads/"""
    if not p: return None
    return p if str(p).startswith(("http://", "https://")) else f"/uploads/{p}"

def _img_bytes(img_value):
    """把 img 地址取回图片字节：本地读 UPLOAD_DIR 下文件，外链用 HTTP 下载。
    返回 (bytes|None, 错误说明|None)。"""
    import urllib.request
    if not img_value:
        return None, "这一层没有照片"
    if img_value.startswith("/uploads/"):
        rel = img_value[len("/uploads/"):].replace("\\", "/")
        if ".." in rel.split("/") or rel.startswith("/"):
            return None, "图片路径非法"
        p = os.path.join(UPLOAD_DIR, rel)
        if not os.path.isfile(p):
            return None, f"图片文件不在服务器上（{img_value}）——多半是 mcp 服务没挂载 uploads 目录"
        with open(p, "rb") as fh:
            raw = fh.read()
        return (raw, None) if raw else (None, "图片文件是空的")
    if img_value.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(img_value, headers={"User-Agent": "wms-mcp/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(15 * 1024 * 1024 + 1)
            if len(raw) > 15 * 1024 * 1024:
                return None, "外链图片过大（>15MB）"
            return (raw, None) if raw else (None, "外链图片下载为空")
        except Exception as e:
            return None, f"外链图片下载失败：{e}"
    return None, "不支持的图片引用"

def _sniff_mime(raw):
    if raw[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if raw[:2] == b"\xff\xd8": return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP": return "image/webp"
    return "image/jpeg"

def _vision_b64(raw, max_edge):
    """把照片压成最长边 ≤ max_edge 的 JPEG 再 base64（省 AI 的上下文）。
    Pillow 处理失败时，原图 ≤3MB 直接回原图。返回 (b64, mime)。"""
    import base64, io
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)); im.load()
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
    except Exception:
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("图片过大且压缩失败")
        return base64.b64encode(raw).decode(), _sniff_mime(raw)

def _slot_code(r, c):
    return chr(65 + r) + "-" + str(c + 1).zfill(2)

def _groups_of(fid):
    """楼层编组列表：[{group_id, name, members: ['r,c'...]}]"""
    import json as _json
    rows = q("SELECT id, name, members FROM slot_groups WHERE floor_id=:fid ORDER BY sort_order, id",
             {"fid": fid})
    out = []
    for g in rows:
        try:
            members = [str(m) for m in _json.loads(g["members"] or "[]")]
        except Exception:
            members = []
        out.append({"group_id": g["id"], "name": g["name"], "members": members})
    return out

def _group_name_of_key(groups, key):
    for g in groups:
        if key in g["members"]:
            return g["name"]
    return None

def _group_pos_map(groups):
    """key → (组名, 组内序号)。序号按阅读顺序：先从上往下排（行），同一行从左往右（列）"""
    out = {}
    for g in groups:
        ms = sorted(g["members"], key=lambda m: tuple(int(v) for v in m.split(",")))
        for i, m in enumerate(ms):
            out[m] = (g["name"], i + 1)
    return out

def _loc(floor_name, group_name, ordinal, code, level_index=None):
    """把位置拼成人话：组名（置物架）优先，再报组内第几个库位，编码只放括号对照。
    例：有组 "1F · 1号货架第2个库位第3层（A-03）"；无组 "1F · A-03 第3层"。"""
    s = (floor_name + " · ") if floor_name else ""
    if group_name:
        s += group_name
        if ordinal:
            s += f"第{ordinal}个库位"
        if level_index is not None:
            s += f"第{level_index + 1}层"
        s += f"（{code}）"
    else:
        s += code
        if level_index is not None:
            s += f" 第{level_index + 1}层"
    return s

def _translate_groups(db, fid, keymap, dropped_keys):
    """移动/覆盖后同步编组成员（成员跟随库位走，不是跟随位置）：
    keymap 的旧 key 换新 key；dropped_keys（被覆盖删除的格子）移出组；成员为空的组删除。
    必须在与移动同一个事务的 db 上执行。"""
    import json as _json
    rows = db.execute(text("SELECT id, members FROM slot_groups WHERE floor_id=:fid"),
                      {"fid": fid}).mappings().all()
    if not rows:
        return
    existing = {f"{s['r']},{s['c']}" for s in
                db.execute(text("SELECT r, c FROM slots WHERE floor_id=:fid"), {"fid": fid}).mappings().all()}
    for g in rows:
        try:
            members = [str(m) for m in _json.loads(g["members"] or "[]")]
        except Exception:
            members = []
        new = []
        for m in members:
            if m in dropped_keys and m not in keymap:
                continue
            nm = keymap.get(m, m)
            if nm in existing and nm not in new:
                new.append(nm)
        if new != members:
            if new:
                db.execute(text("UPDATE slot_groups SET members=:mem WHERE id=:gid"),
                           {"mem": _json.dumps(new), "gid": g["id"]})
            else:
                db.execute(text("DELETE FROM slot_groups WHERE id=:gid"), {"gid": g["id"]})

def _image_tool(a, single):
    """wms_view_image / wms_view_images：返回 文本索引 + ImageContent 列表。"""
    fid = a["floor_id"]
    fl = q_one("SELECT id, name FROM floors WHERE id=:fid", {"fid": fid})
    if not fl: raise ValueError("楼层不存在")
    fname = fl["name"]
    if single:
        rc = code_to_rc(str(a.get("code", "")))
        if not rc: raise ValueError("库位编码格式错误，应为如 B-04")
        slot = q_one("SELECT id, r, c FROM slots WHERE floor_id=:fid AND r=:r AND c=:c",
                     {"fid": fid, "r": rc[0], "c": rc[1]})
        if not slot: raise ValueError("库位不存在")
        idx = int(a["level_index"])
        lv = q_one("SELECT name, qty, status_key, note, img_path FROM levels WHERE slot_id=:sid AND level_index=:idx",
                   {"sid": slot["id"], "idx": idx})
        if not lv or not lv["img_path"]: raise ValueError("这一层没有照片")
        raw, err = _img_bytes(_img_out(lv["img_path"]))
        if err: raise ValueError(err)
        b64, mime = _vision_b64(raw, 1024)
        gname1, gord1 = _group_pos_map(_groups_of(fid)).get(f"{slot['r']},{slot['c']}", (None, None))
        label = f"{_loc(fname, gname1, gord1, _slot_code(slot['r'], slot['c']), idx)}「{lv['name']}」x{lv['qty']}"
        if lv["note"]: label += f"（备注：{lv['note']}）"
        return [TextContent(type="text", text=label + " 的照片："),
                ImageContent(type="image", data=b64, mimeType=mime)]

    limit = min(12, max(1, int(a.get("limit") or 6)))
    offset = max(0, int(a.get("offset") or 0))
    where = ["s.floor_id=:fid", "l.img_path IS NOT NULL", "l.img_path <> ''"]
    params = {"fid": fid}
    if a.get("code"):
        rc = code_to_rc(str(a["code"]))
        if not rc: raise ValueError("库位编码格式错误，应为如 B-04")
        where.append("s.r=:r AND s.c=:c"); params.update({"r": rc[0], "c": rc[1]})
    if a.get("keyword"):
        where.append("l.name LIKE :kw"); params["kw"] = f"%{a['keyword']}%"
    w = " AND ".join(where)
    total = q_one(f"""SELECT COUNT(*) AS n FROM levels l JOIN slots s ON s.id=l.slot_id
                      WHERE {w}""", params)["n"]
    rows = q(f"""SELECT s.r, s.c, l.level_index, l.name, l.qty, l.note, l.img_path
                 FROM levels l JOIN slots s ON s.id=l.slot_id
                 WHERE {w} ORDER BY s.r, s.c, l.level_index LIMIT :lim OFFSET :off""",
             {**params, "lim": limit, "off": offset})
    contents, lines, shown = [], [], 0
    pos_map = _group_pos_map(_groups_of(fid))
    for row in rows:
        n = offset + shown + 1
        gnameB, gordB = pos_map.get(f"{row['r']},{row['c']}", (None, None))
        pos = f"{_loc(fname, gnameB, gordB, _slot_code(row['r'], row['c']), row['level_index'])}「{row['name']}」x{row['qty']}"
        raw, err = _img_bytes(_img_out(row["img_path"]))
        if err:
            lines.append(f"图{n} {pos}（读取失败：{err}）")
            continue
        b64, mime = _vision_b64(raw, 640)
        lines.append(f"图{n} {pos}")
        contents.append(ImageContent(type="image", data=b64, mimeType=mime))
        shown += 1
    head = f"{fname} 共 {total} 张货物照片，这次第 {offset + 1}–{offset + len(rows)} 张："
    if offset + len(rows) < total:
        head += f"\n（还有 {total - offset - len(rows)} 张没看，换 offset={offset + len(rows)} 继续）"
    if not total:
        head = f"{fname} 没有符合条件的货物照片。"
    return [TextContent(type="text", text=head + ("\n" + "\n".join(lines) if lines else ""))] + contents

@server.list_tools()
async def list_tools():
    return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    import json
    try:
        a = arguments or {}
        if name == "wms_list_warehouses":
            result = q("SELECT id, name FROM warehouses ORDER BY id")
        elif name == "wms_list_floors":
            result = q("SELECT id, name, rows, cols FROM floors WHERE warehouse_id=:wid ORDER BY sort_order,id",
                       {"wid": a["warehouse_id"]})
        elif name == "wms_get_floor_map":
            fid = a["floor_id"]
            slots = q("SELECT id, r, c FROM slots WHERE floor_id=:fid", {"fid": fid})
            sids = [s["id"] for s in slots]
            if sids:
                ph = ",".join(f":s{i}" for i in range(len(sids)))
                levels = q(f"SELECT slot_id, level_index, status_key, name, qty, note, img_path FROM levels WHERE slot_id IN ({ph}) ORDER BY slot_id, level_index",
                           {f"s{i}": v for i, v in enumerate(sids)})
            else:
                levels = []
            by_slot = {}
            for lv in levels:
                lv["img"] = _img_out(lv.pop("img_path", None))
                by_slot.setdefault(lv["slot_id"], []).append(lv)
            out = []
            groups = _groups_of(fid)
            pos_map = _group_pos_map(groups)
            fl_ = q_one("SELECT name FROM floors WHERE id=:fid", {"fid": fid})
            fname_ = fl_["name"] if fl_ else ""
            for s in slots:
                code = chr(65 + s["r"]) + "-" + str(s["c"] + 1).zfill(2)
                gname_, gord_ = pos_map.get(f"{s['r']},{s['c']}", (None, None))
                out.append({"code": code, "levels": by_slot.get(s["id"], []),
                            "group": gname_, "group_index": gord_,
                            "location": _loc(fname_, gname_, gord_, code)})
            zones = q("SELECT name, r0, c0, r1, c1 FROM zones WHERE floor_id=:fid", {"fid": fid})
            result = {"slots": out, "zones": zones,
                      "groups": [{"group_id": g["group_id"], "name": g["name"],
                                  "members": [_slot_code(*[int(v) for v in m.split(",")]) for m in g["members"]]}
                                 for g in groups]}
        elif name == "wms_search_goods":
            sql = """SELECT w.name AS warehouse, f.name AS floor, f.id AS floor_id, s.r, s.c,
                            l.level_index, l.status_key, l.name, l.qty, l.note, l.img_path
                     FROM levels l JOIN slots s ON s.id=l.slot_id
                     JOIN floors f ON f.id=s.floor_id JOIN warehouses w ON w.id=f.warehouse_id
                     WHERE lower(l.name) LIKE :kw"""
            params = {"kw": f"%{str(a['keyword']).lower()}%"}
            if a.get("warehouse_id"):
                sql += " AND w.id=:wid"; params["wid"] = a["warehouse_id"]
            sql += " ORDER BY w.id,f.id,s.r,s.c,l.level_index LIMIT 100"
            result = q(sql, params)
            gcache = {}
            for row in result:
                row["img"] = _img_out(row.pop("img_path", None))
                code_ = _slot_code(row["r"], row["c"])
                row["code"] = code_
                fid_ = row.get("floor_id")
                if fid_ not in gcache:
                    gcache[fid_] = _group_pos_map(_groups_of(fid_))
                gname, gord = gcache[fid_].get(f"{row['r']},{row['c']}", (None, None))
                row["group"] = gname
                row["group_index"] = gord
                row["location"] = _loc(row.get("floor"), gname, gord, code_, row.get("level_index"))
                row.pop("floor_id", None); row.pop("r", None); row.pop("c", None)
        elif name == "wms_get_slot":
            rc = code_to_rc(a["code"])
            if not rc: raise ValueError("库位编码格式错误，应为如 B-04")
            s = q_one("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c",
                      {"fid": a["floor_id"], "r": rc[0], "c": rc[1]})
            if not s:
                result = {"code": a["code"], "exists": False, "levels": []}
            else:
                levels = q("SELECT level_index, status_key, name, qty, note, img_path FROM levels WHERE slot_id=:sid ORDER BY level_index",
                           {"sid": s["id"]})
                fl_ = q_one("SELECT name FROM floors WHERE id=:fid", {"fid": a["floor_id"]})
                fname_ = fl_["name"] if fl_ else ""
                groups = _groups_of(a["floor_id"])
                key = f"{rc[0]},{rc[1]}"
                gname, gord = _group_pos_map(groups).get(key, (None, None))
                for lv in levels:
                    lv["img"] = _img_out(lv.pop("img_path", None))
                    lv["location"] = _loc(fname_, gname, gord, a["code"], lv.get("level_index"))
                result = {"code": a["code"], "exists": True, "levels": levels, "group": gname,
                          "group_index": gord,
                          "location": _loc(fname_, gname, gord, a["code"])}
        elif name == "wms_update_level":
            rc = code_to_rc(a["code"])
            if not rc: raise ValueError("库位编码格式错误")
            db = Session()
            try:
                s = db.execute(text("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c"),
                               {"fid": a["floor_id"], "r": rc[0], "c": rc[1]}).mappings().first()
                sid = s["id"] if s else db.execute(
                    text("INSERT INTO slots (floor_id,r,c) VALUES (:fid,:r,:c) RETURNING id"),
                    {"fid": a["floor_id"], "r": rc[0], "c": rc[1]}).mappings().first()["id"]
                colvals = {}
                if "status_key" in a: colvals["status_key"] = str(a["status_key"])[:32]
                if "name" in a: colvals["name"] = str(a["name"])[:64]
                if "qty" in a: colvals["qty"] = max(0, int(a["qty"]))
                if "note" in a: colvals["note"] = str(a["note"])[:500]
                if "img" in a:
                    v = a["img"]
                    if isinstance(v, str) and v.startswith("/uploads/"):
                        colvals["img_path"] = v[len("/uploads/"):]
                    elif isinstance(v, str) and v.startswith(("http://", "https://")):
                        colvals["img_path"] = v
                    elif v in ("", None):
                        colvals["img_path"] = None
                    else:
                        raise ValueError("img 仅支持 /uploads/ 路径、http(s) 链接或空字符串")
                if not colvals: raise ValueError("没有要更新的字段")
                ins = {"status_key": "empty", "name": "", "qty": 0, "note": "", "img_path": None}
                ins.update(colvals)
                params = {"sid": sid, "idx": a["level_index"], **ins}
                db.execute(text(f"""INSERT INTO levels (slot_id,level_index,status_key,name,qty,img_path,note)
                                    VALUES (:sid,:idx,:status_key,:name,:qty,:img_path,:note)
                                    ON CONFLICT (slot_id,level_index) DO UPDATE SET {','.join(k + '=:' + k for k in colvals)}"""), params)
                db.execute(text("INSERT INTO op_logs (action, detail) VALUES ('mcp.update_level', :d)"),
                           {"d": f"{a['code']} 第{a['level_index']+1}层"})
                db.commit()
                result = {"ok": True}
            finally:
                db.close()
        elif name in ("wms_move_slot", "wms_copy_slot"):
            frc = code_to_rc(a["from_code"]); trc = code_to_rc(a["to_code"])
            if not frc or not trc: raise ValueError("库位编码格式错误，应为如 B-04")
            if frc == trc: raise ValueError("源和目标是同一个格子")
            db = Session()
            try:
                src = db.execute(text("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c"),
                                 {"fid": a["floor_id"], "r": frc[0], "c": frc[1]}).mappings().first()
                if not src: raise ValueError(f"源库位 {a['from_code']} 不存在")
                sid = src["id"]
                dst = db.execute(text("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c"),
                                 {"fid": a["floor_id"], "r": trc[0], "c": trc[1]}).mappings().first()
                overwritten = False
                if dst:
                    if not a.get("overwrite"):
                        raise ValueError(f"目标 {a['to_code']} 已有库位；确认覆盖请传 overwrite=true")
                    db.execute(text("DELETE FROM levels WHERE slot_id=:tid"), {"tid": dst["id"]})
                    db.execute(text("DELETE FROM slots WHERE id=:tid"), {"tid": dst["id"]})
                    overwritten = True
                if name == "wms_move_slot":
                    db.execute(text("UPDATE slots SET r=:r, c=:c WHERE id=:sid"),
                               {"r": trc[0], "c": trc[1], "sid": sid})
                    nsid = sid
                else:
                    nsid = db.execute(text("INSERT INTO slots (floor_id,r,c) VALUES (:fid,:r,:c) RETURNING id"),
                                      {"fid": a["floor_id"], "r": trc[0], "c": trc[1]}).mappings().first()["id"]
                    db.execute(text("""INSERT INTO levels (slot_id,level_index,status_key,name,qty,img_path,note)
                                       SELECT :nsid, level_index,status_key,name,qty,img_path,note
                                       FROM levels WHERE slot_id=:sid"""), {"nsid": nsid, "sid": sid})
                n_levels = db.execute(text("SELECT COUNT(*) AS n FROM levels WHERE slot_id=:sid"),
                                      {"sid": nsid}).mappings().first()["n"]
                db.execute(text("INSERT INTO op_logs (action, detail) VALUES (:act, :d)"),
                           {"act": f"mcp.{name}", "d": f"{a['from_code']} -> {a['to_code']}"})
                _translate_groups(db, a["floor_id"],
                                  {f"{frc[0]},{frc[1]}": f"{trc[0]},{trc[1]}"} if name == "wms_move_slot" else {},
                                  {f"{trc[0]},{trc[1]}"} if overwritten else set())
                db.commit()
                result = {"ok": True, "from": a["from_code"], "to": a["to_code"],
                          "levels": n_levels, "overwritten": overwritten}
            finally:
                db.close()
        elif name in ("wms_move_slots", "wms_copy_slots"):
            items = a.get("items") or []
            if not items: raise ValueError("items 为空")
            fid = a["floor_id"]
            pairs = []  # (from_code, to_code, (fr,fc), (tr,tc))
            for it in items:
                frc = code_to_rc(str(it.get("from_code", ""))); trc = code_to_rc(str(it.get("to_code", "")))
                if not frc or not trc: raise ValueError("库位编码格式错误，应为如 B-04")
                if frc == trc: raise ValueError(f"{it.get('from_code')} 源和目标相同")
                pairs.append((it["from_code"], it["to_code"], frc, trc))
            toc = [p[1] for p in pairs]
            if len(set(toc)) != len(toc): raise ValueError("目标库位有重复")
            from_set = {p[0] for p in pairs}
            if name == "wms_move_slots" and len(from_set) != len(pairs):
                raise ValueError("批量移动时源库位不能重复")
            db = Session()
            try:
                src_ids = {}
                for fc_, tc_, frc, trc in pairs:
                    s = db.execute(text("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c"),
                                   {"fid": fid, "r": frc[0], "c": frc[1]}).mappings().first()
                    if not s: raise ValueError(f"源库位 {fc_} 不存在")
                    src_ids[fc_] = s["id"]
                    src_ids[fc_] = s["id"]
                # 复制先把源层级快照到内存：之后即使目标与其它源重叠、要删源格，也不丢数据
                snaps = {}
                if name == "wms_copy_slots":
                    for fc_, tc_, frc, trc in pairs:
                        snaps[fc_] = db.execute(text(
                            "SELECT level_index,status_key,name,qty,img_path,note FROM levels WHERE slot_id=:sid ORDER BY level_index"),
                            {"sid": src_ids[fc_]}).mappings().all()
                overwritten = []
                for fc_, tc_, frc, trc in pairs:
                    d = db.execute(text("SELECT id FROM slots WHERE floor_id=:fid AND r=:r AND c=:c"),
                                   {"fid": fid, "r": trc[0], "c": trc[1]}).mappings().first()
                    if not d: continue
                    # 移动时目标若是本批次源格，留给源格自己移走；其余占用都需先清
                    if name == "wms_move_slots" and tc_ in from_set: continue
                    if not a.get("overwrite"):
                        raise ValueError(f"目标 {tc_} 已有库位；确认覆盖请传 overwrite=true")
                    db.execute(text("DELETE FROM levels WHERE slot_id=:tid"), {"tid": d["id"]})
                    db.execute(text("DELETE FROM slots WHERE id=:tid"), {"tid": d["id"]})
                    overwritten.append(tc_)
                if name == "wms_move_slots":
                    # 先把源格挪到临时负坐标，避开连环移动的唯一约束冲突，再逐个落位
                    for i, (fc_, tc_, frc, trc) in enumerate(pairs):
                        db.execute(text("UPDATE slots SET r=:r, c=:c WHERE id=:sid"),
                                   {"r": -10000 - i, "c": -10000 - i, "sid": src_ids[fc_]})
                    for fc_, tc_, frc, trc in pairs:
                        db.execute(text("UPDATE slots SET r=:r, c=:c WHERE id=:sid"),
                                   {"r": trc[0], "c": trc[1], "sid": src_ids[fc_]})
                else:
                    for fc_, tc_, frc, trc in pairs:
                        nsid = db.execute(text("INSERT INTO slots (floor_id,r,c) VALUES (:fid,:r,:c) RETURNING id"),
                                          {"fid": fid, "r": trc[0], "c": trc[1]}).mappings().first()["id"]
                        for lv in snaps[fc_]:
                            db.execute(text("""INSERT INTO levels (slot_id,level_index,status_key,name,qty,img_path,note)
                                               VALUES (:nsid,:li,:sk,:nm,:qty,:img,:note)"""),
                                       {"nsid": nsid, "li": lv["level_index"], "sk": lv["status_key"], "nm": lv["name"],
                                        "qty": lv["qty"], "img": lv["img_path"], "note": lv["note"]})
                db.execute(text("INSERT INTO op_logs (action, detail) VALUES (:act, :d)"),
                           {"act": f"mcp.{name}", "d": f"批量{len(pairs)}个: " + ", ".join(f"{p[0]}->{p[1]}" for p in pairs)})
                if name == "wms_move_slots":
                    keymap = {f"{p[2][0]},{p[2][1]}": f"{p[3][0]},{p[3][1]}" for p in pairs}
                    dropped = {f"{p[3][0]},{p[3][1]}" for p in pairs if p[1] in overwritten}
                    _translate_groups(db, fid, keymap, dropped)
                elif overwritten:
                    dropped = {f"{p[3][0]},{p[3][1]}" for p in pairs if p[1] in overwritten}
                    _translate_groups(db, fid, {}, dropped)
                db.commit()
                result = {"ok": True, "count": len(pairs), "overwritten": overwritten}
            finally:
                db.close()
        elif name == "wms_list_groups":
            fid = a["floor_id"]
            if not q_one("SELECT id FROM floors WHERE id=:fid", {"fid": fid}):
                raise ValueError("楼层不存在")
            gs = _groups_of(fid)
            slots_by_key = {f"{s['r']},{s['c']}": s for s in q("SELECT r, c FROM slots WHERE floor_id=:fid", {"fid": fid})}
            result = [{"group_id": g["group_id"], "name": g["name"],
                       "members": [_slot_code(*[int(v) for v in m.split(",")]) for m in g["members"] if m in slots_by_key],
                       "count": len(g["members"])} for g in gs]
        elif name == "wms_create_group":
            fid = a["floor_id"]
            name_g = str(a.get("name", "")).strip()
            if not name_g: raise ValueError("编组名称不能为空")
            if not q_one("SELECT id FROM floors WHERE id=:fid", {"fid": fid}):
                raise ValueError("楼层不存在")
            keys, seen = [], set()
            for cd in a.get("codes") or []:
                rc = code_to_rc(str(cd))
                if not rc: raise ValueError(f"库位编码格式错误：{cd}")
                k = f"{rc[0]},{rc[1]}"
                if k not in seen: seen.add(k); keys.append(k)
            if not keys: raise ValueError("至少要选一个库位")
            slots_by_key = {f"{s['r']},{s['c']}": s for s in q("SELECT r, c FROM slots WHERE floor_id=:fid", {"fid": fid})}
            missing = [k for k in keys if k not in slots_by_key]
            if missing: raise ValueError("这些库位不存在：" + ", ".join(_slot_code(*[int(v) for v in k.split(",")]) for k in missing))
            existing = _groups_of(fid)
            for k in keys:
                gname = _group_name_of_key(existing, k)
                if gname: raise ValueError(f"{_slot_code(*[int(v) for v in k.split(',')])} 已在编组「{gname}」中，一个库位只能属于一个编组")
            db = Session()
            try:
                so = db.execute(text("SELECT COALESCE(MAX(sort_order), -1) AS m FROM slot_groups WHERE floor_id=:fid"),
                                {"fid": fid}).mappings().first()["m"] + 1
                gid = db.execute(text("INSERT INTO slot_groups (floor_id, name, members, sort_order) VALUES (:fid,:nm,:mem,:so) RETURNING id"),
                                 {"fid": fid, "nm": name_g[:64], "mem": json.dumps(keys), "so": so}).mappings().first()["id"]
                db.commit()
            finally:
                db.close()
            result = {"ok": True, "group_id": gid, "name": name_g[:64],
                      "members": [_slot_code(*[int(v) for v in k.split(",")]) for k in keys]}
        elif name == "wms_update_group":
            fid, gid = a["floor_id"], int(a["group_id"])
            g = q_one("SELECT id, name, members FROM slot_groups WHERE id=:gid AND floor_id=:fid",
                      {"gid": gid, "fid": fid})
            if not g: raise ValueError("编组不存在")
            new_name, new_members = None, None
            if a.get("name") is not None:
                new_name = str(a["name"]).strip()
                if not new_name: raise ValueError("编组名称不能为空")
                new_name = new_name[:64]
            if a.get("codes") is not None:
                keys, seen = [], set()
                for cd in a["codes"] or []:
                    rc = code_to_rc(str(cd))
                    if not rc: raise ValueError(f"库位编码格式错误：{cd}")
                    k = f"{rc[0]},{rc[1]}"
                    if k not in seen: seen.add(k); keys.append(k)
                slots_by_key = {f"{s['r']},{s['c']}": s for s in q("SELECT r, c FROM slots WHERE floor_id=:fid", {"fid": fid})}
                missing = [k for k in keys if k not in slots_by_key]
                if missing: raise ValueError("这些库位不存在：" + ", ".join(_slot_code(*[int(v) for v in k.split(",")]) for k in missing))
                for k in keys:
                    for x in _groups_of(fid):
                        if x["group_id"] != gid and k in x["members"]:
                            raise ValueError(f"{_slot_code(*[int(v) for v in k.split(',')])} 已在编组「{x['name']}」中")
                new_members = keys
            if new_name is None and new_members is None:
                raise ValueError("没有要更新的字段（name 或 codes）")
            db = Session()
            try:
                sets, params = [], {"gid": gid}
                if new_name is not None: sets.append("name=:nm"); params["nm"] = new_name
                if new_members is not None: sets.append("members=:mem"); params["mem"] = json.dumps(new_members)
                db.execute(text(f"UPDATE slot_groups SET {', '.join(sets)} WHERE id=:gid"), params)
                db.commit()
            finally:
                db.close()
            result = {"ok": True, "group_id": gid, "name": new_name if new_name is not None else g["name"],
                      "members": ([_slot_code(*[int(v) for v in m.split(",")]) for m in new_members]
                                  if new_members is not None else None)}
        elif name == "wms_delete_group":
            fid, gid = a["floor_id"], int(a["group_id"])
            g = q_one("SELECT id, name FROM slot_groups WHERE id=:gid AND floor_id=:fid",
                      {"gid": gid, "fid": fid})
            if not g: raise ValueError("编组不存在")
            db = Session()
            try:
                db.execute(text("DELETE FROM slot_groups WHERE id=:gid"), {"gid": gid})
                db.commit()
            finally:
                db.close()
            result = {"ok": True, "deleted": g["name"]}
        elif name == "wms_inventory_summary":
            sql = """SELECT l.status_key, COUNT(*) AS levels, COALESCE(SUM(l.qty),0) AS qty
                     FROM levels l JOIN slots s ON s.id=l.slot_id JOIN floors f ON f.id=s.floor_id"""
            params = {}
            if a.get("warehouse_id"):
                sql += " WHERE f.warehouse_id=:wid"; params["wid"] = a["warehouse_id"]
            sql += " GROUP BY l.status_key"
            result = q(sql, params)
        elif name == "wms_list_zones":
            result = q("SELECT name, r0, c0, r1, c1 FROM zones WHERE floor_id=:fid ORDER BY id",
                       {"fid": a["floor_id"]})
        elif name in ("wms_view_image", "wms_view_images"):
            return _image_tool(a, single=(name == "wms_view_image"))
        else:
            raise ValueError(f"未知工具: {name}")
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2, default=str))]
    except Exception as e:
        import json
        return [TextContent(type="text", text=f"错误: {e}")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
