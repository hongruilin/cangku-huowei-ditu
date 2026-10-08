# WMS MCP Server (Python)：供 AI 增删改查仓库数据
# 运行：python mcp/server.py （stdio 模式）
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wms:wms@localhost:5432/wms")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

server = Server("wms-mcp")

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
            levels = q("SELECT slot_id, level_index, status_key, name, qty, note, img_path FROM levels WHERE slot_id = ANY(:sids) ORDER BY slot_id, level_index",
                       {"sids": sids}) if sids else []
            by_slot = {}
            for lv in levels:
                lv["img"] = _img_out(lv.pop("img_path", None))
                by_slot.setdefault(lv["slot_id"], []).append(lv)
            out = []
            for s in slots:
                code = chr(65 + s["r"]) + "-" + str(s["c"] + 1).zfill(2)
                out.append({"code": code, "levels": by_slot.get(s["id"], [])})
            zones = q("SELECT name, r0, c0, r1, c1 FROM zones WHERE floor_id=:fid", {"fid": fid})
            result = {"slots": out, "zones": zones}
        elif name == "wms_search_goods":
            sql = """SELECT w.name AS warehouse, f.name AS floor,
                            CHR(65+s.r) || '-' || LPAD((s.c+1)::text,2,'0') AS code,
                            l.level_index, l.status_key, l.name, l.qty, l.note, l.img_path
                     FROM levels l JOIN slots s ON s.id=l.slot_id
                     JOIN floors f ON f.id=s.floor_id JOIN warehouses w ON w.id=f.warehouse_id
                     WHERE l.name ILIKE :kw"""
            params = {"kw": f"%{a['keyword']}%"}
            if a.get("warehouse_id"):
                sql += " AND w.id=:wid"; params["wid"] = a["warehouse_id"]
            sql += " ORDER BY w.id,f.id,s.r,s.c,l.level_index LIMIT 100"
            result = q(sql, params)
            for row in result:
                row["img"] = _img_out(row.pop("img_path", None))
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
                for lv in levels:
                    lv["img"] = _img_out(lv.pop("img_path", None))
                result = {"code": a["code"], "exists": True, "levels": levels}
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
                db.commit()
                result = {"ok": True, "count": len(pairs), "overwritten": overwritten}
            finally:
                db.close()
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
