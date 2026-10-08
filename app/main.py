import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from .db import engine, Base, SessionLocal
from . import models
from .routers import auth as auth_router, warehouses as wh_router, map as map_router
from .routers import upload as upload_router, backup as backup_router

app = FastAPI(title="WMS 仓库管理系统", version="2.0.0")

# Session 中间件：签名 cookie，7 天有效
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "wms-session-secret-change-me"),
    max_age=7 * 24 * 3600,
    same_site="lax",
)

app.include_router(auth_router.router)
app.include_router(wh_router.router)
app.include_router(map_router.router)
app.include_router(upload_router.router)
app.include_router(backup_router.router)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "..", "public")
if os.path.isdir(PUBLIC_DIR):
    # 首页禁用缓存
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(PUBLIC_DIR, "index.html"),
                            headers={"Cache-Control": "no-store"})
    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")

@app.get("/api/health")
def health():
    from datetime import datetime
    return {"ok": True, "time": datetime.now().isoformat(), "version": get_version()}

@app.get("/api/version")
def version():
    return {"version": get_version()}

def get_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "VERSION")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "dev"

def init_db():
    Base.metadata.create_all(bind=engine)
    # 老库补列：levels.note（create_all 不会给已存在的表加列）
    from sqlalchemy import inspect, text
    if "levels" in inspect(engine).get_table_names():
        cols = [c["name"] for c in inspect(engine).get_columns("levels")]
        if "note" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE levels ADD COLUMN note TEXT DEFAULT ''"))
            print("[db] migrated: levels.note added")
    db = SessionLocal()
    try:
        # 内置状态
        builtin = [("empty", "空", "#e2e8f0"), ("full", "有货", "#bbf7d0"),
                   ("in", "入库中", "#bfdbfe"), ("locked", "锁定", "#fecaca")]
        for key, name, color in builtin:
            if not db.query(models.Status).filter(models.Status.warehouse_id.is_(None),
                                                  models.Status.key == key).first():
                db.add(models.Status(warehouse_id=None, key=key, name=name, color=color, builtin=True))
        # 管理员
        admin_user = os.getenv("ADMIN_USER", "admin")
        if not db.query(models.User).filter(models.User.username == admin_user).first():
            import bcrypt
            db.add(models.User(username=admin_user,
                               password_hash=bcrypt.hashpw(os.getenv("ADMIN_PASS", "admin123").encode(), bcrypt.gensalt()).decode(),
                               role="admin"))
            print(f"[db] seeded admin: {admin_user}")
        db.commit()
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    init_db()
    print("[wms] ready")
