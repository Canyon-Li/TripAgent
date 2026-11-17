"""数据表模型（§9 DDL）：runs / spans（阶段 0）+ memory_events / memory_views（阶段 2）。"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Run(Base):
    __tablename__ = "runs"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    # running | succeeded | failed | interrupted
    status: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    git_sha: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO-8601
    finished_at: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_in: Mapped[int | None] = mapped_column(Integer)
    token_out: Mapped[int | None] = mapped_column(Integer)


class Span(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.trace_id"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    meta_json: Mapped[str | None] = mapped_column(Text)


class MemoryEvent(Base):
    """Ledger：追加写、不可变（ADR-003）。矛盾用 supersedes_id 归档，不物理删除。"""

    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    # assertion | belief | decision | observation（§9）
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    # user_stated | behavior | environment | agent_generated（Provenance）
    source: Mapped[str] = mapped_column(String, nullable=False)
    # session:<sid> | user:<uid> | global（§9 的 trip:<id> 按实际语义落为 session）
    scope: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    decay_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    supersedes_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("memory_events.id")
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO-8601


class MemoryView(Base):
    """派生视图（可从 Ledger 重放重建，§7.2）；valid_to 非空 = 已被更新版本替代。"""

    __tablename__ = "memory_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # hard_constraints | persona | summary（§9）
    view_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_key: Mapped[str] = mapped_column(String, nullable=False)  # = scope
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    derived_from: Mapped[str] = mapped_column(Text, nullable=False)  # event id list
    valid_from: Mapped[str] = mapped_column(String, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(String)
    last_confirmed_at: Mapped[str | None] = mapped_column(String)
