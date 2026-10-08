from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.sql import func
from .db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(16), default="operator")  # admin/operator/viewer
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Floor(Base):
    __tablename__ = "floors"
    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(64), nullable=False)
    rows = Column(Integer, default=8)
    cols = Column(Integer, default=14)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Status(Base):
    __tablename__ = "statuses"
    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=True)
    key = Column(String(32), nullable=False)
    name = Column(String(64), nullable=False)
    color = Column(String(16), default="#e2e8f0")
    builtin = Column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("warehouse_id", "key", name="uq_status_wh_key"),)

class Zone(Base):
    __tablename__ = "zones"
    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    r0 = Column(Integer, default=0); c0 = Column(Integer, default=0)
    r1 = Column(Integer, default=0); c1 = Column(Integer, default=0)

class Slot(Base):
    __tablename__ = "slots"
    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    r = Column(Integer, nullable=False); c = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("floor_id", "r", "c", name="uq_slot_floor_rc"),)

class Level(Base):
    __tablename__ = "levels"
    id = Column(Integer, primary_key=True)
    slot_id = Column(Integer, ForeignKey("slots.id", ondelete="CASCADE"), nullable=False)
    level_index = Column(Integer, nullable=False)
    status_key = Column(String(32), default="empty")
    name = Column(String(64), default="")
    qty = Column(Integer, default=0)
    img_path = Column(String(255), nullable=True)
    note = Column(Text, default="")  # 每层可选备注
    __table_args__ = (UniqueConstraint("slot_id", "level_index", name="uq_level_slot_idx"),)

class SlotGroup(Base):
    """库位编组：把多个库位组合成一个置物架并命名。members 是 JSON 数组 ["r,c", ...]"""
    __tablename__ = "slot_groups"
    id = Column(Integer, primary_key=True)
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(64), nullable=False)
    members = Column(Text, default="[]")
    sort_order = Column(Integer, default=0)

class OpLog(Base):
    __tablename__ = "op_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(64), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
