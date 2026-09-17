from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RouterRecord(Base):
    __tablename__ = "routers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(128))
    gateway: Mapped[str] = mapped_column(String(128))
    reverse_port: Mapped[int] = mapped_column(Integer)
    egress_policy: Mapped[str] = mapped_column(String(32), default="foreign")
    enabled: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HealthSample(Base):
    __tablename__ = "health_samples"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid())
    router_id: Mapped[str] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transport_ok: Mapped[bool] = mapped_column(default=False)
    process_ok: Mapped[bool] = mapped_column(default=False)
    service_ok: Mapped[bool] = mapped_column(default=False)
    tun_ok: Mapped[bool] = mapped_column(default=False)
    foreign_ip_ok: Mapped[bool] = mapped_column(default=False)
    load1: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    vsz_kb: Mapped[int | None] = mapped_column(Integer)
    rss_kb: Mapped[int | None] = mapped_column(Integer)
    mem_available_kb: Mapped[int | None] = mapped_column(Integer)
    disk_used_percent: Mapped[float | None] = mapped_column(Float)
    restart_count: Mapped[int | None] = mapped_column(Integer)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    __tablename__ = "monitor_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid())
    router_id: Mapped[str] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class BaselineProfile(Base):
    __tablename__ = "baseline_profiles"
    __table_args__ = (UniqueConstraint("router_id", "metric", "window"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid())
    router_id: Mapped[str] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    window: Mapped[str] = mapped_column(String(32))
    median_value: Mapped[float] = mapped_column(Float)
    p95_value: Mapped[float] = mapped_column(Float)
    mad_value: Mapped[float] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    learning: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
