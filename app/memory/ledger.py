"""Ledger：memory_events 追加写与查询（§7.2 写入链路 / §9 DDL）。

铁律（ADR-003）：只 INSERT，永不 UPDATE / DELETE；矛盾归档 = 新事件 supersedes_id
指向旧事件。所有读取都从这里出发，Views 仅是派生缓存（views.py 可随时重建）。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import MemoryEvent

# §9 event_type / source 枚举（写入侧校验用）
EVENT_TYPES = frozenset({"assertion", "belief", "decision", "observation"})
SOURCES = frozenset({"user_stated", "behavior", "environment", "agent_generated"})


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")  # 与 db/runs.py 同模式


def _to_dict(e: MemoryEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "session_id": e.session_id,
        "user_id": e.user_id,
        "event_type": e.event_type,
        "content": json.loads(e.content_json),
        "source": e.source,
        "scope": e.scope,
        "confidence": e.confidence,
        "decay_weight": e.decay_weight,
        "supersedes_id": e.supersedes_id,
        "created_at": e.created_at,
    }


def append_event(
    *,
    session_id: str,
    user_id: str,
    event_type: str,
    content: dict[str, Any],
    source: str,
    scope: str,
    confidence: float = 1.0,
    supersedes_id: int | None = None,
) -> int:
    """追加一条事件，返回自增 id。调用方（Policy）保证 event_type/source 合法。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type: {event_type!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    with SessionLocal() as db:
        ev = MemoryEvent(
            session_id=session_id,
            user_id=user_id,
            event_type=event_type,
            content_json=json.dumps(content, ensure_ascii=False),
            source=source,
            scope=scope,
            confidence=confidence,
            decay_weight=1.0,
            supersedes_id=supersedes_id,
            created_at=_now_iso(),
        )
        db.add(ev)
        db.commit()
        return ev.id


def get_event(event_id: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        e = db.get(MemoryEvent, event_id)
        return _to_dict(e) if e else None


def list_events(
    *,
    scope: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按条件查询（回放 / 演示用），id 升序 = 追加序。"""
    with SessionLocal() as db:
        q = select(MemoryEvent).order_by(MemoryEvent.id)
        if scope is not None:
            q = q.where(MemoryEvent.scope == scope)
        if session_id is not None:
            q = q.where(MemoryEvent.session_id == session_id)
        if user_id is not None:
            q = q.where(MemoryEvent.user_id == user_id)
        if event_type is not None:
            q = q.where(MemoryEvent.event_type == event_type)
        if limit is not None:
            q = q.limit(limit)
        return [_to_dict(e) for e in db.scalars(q).all()]


def _superseded_ids(scope: str) -> set[int]:
    """该 scope 内被 supersedes_id 指向的事件集合（归档位）。"""
    with SessionLocal() as db:
        rows = db.scalars(
            select(MemoryEvent.supersedes_id).where(
                MemoryEvent.scope == scope, MemoryEvent.supersedes_id.is_not(None)
            )
        ).all()
        return {rid for rid in rows if rid is not None}


def active_assertions(scope: str) -> list[dict[str, Any]]:
    """scope 下未被归档的 assertion（Policy 保证 supersede 只发生在同 scope 内）。"""
    archived = _superseded_ids(scope)
    events = list_events(scope=scope, event_type="assertion")
    return [e for e in events if e["id"] not in archived]


def find_active_assertion(scope: str, field: str) -> dict[str, Any] | None:
    """某字段当前活跃的 assertion（同字段多活跃时取最新——正常流转不会发生）。"""
    hits = [e for e in active_assertions(scope) if e["content"].get("field") == field]
    return hits[-1] if hits else None


def observation_count(session_id: str) -> int:
    """session 内用户轮次原话计数 = turn 计数基础（§7.2 turn 溯源）。"""
    return len(list_events(session_id=session_id, event_type="observation"))
