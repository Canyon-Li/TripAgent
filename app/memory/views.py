"""Views：由 Ledger 派生的可检索状态（§7.2），可随时重放重建（§13「可回放 Ledger」）。

hard_constraints view 的 value_json 结构（recall_constraint 与 Memory.read 同源，§7.6）：
    {"constraints": {field: value, ...},
     "provenance": {field: {event_id, turn, confidence, source}, ...}}
derived_from 为派生所用的 event id 列表（§9），重建按它校验一致性。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import MemoryView
from app.memory import ledger

VIEW_TYPE = "hard_constraints"


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def _view_dict(v: MemoryView) -> dict[str, Any]:
    return {
        "id": v.id,
        "view_type": v.view_type,
        "subject_key": v.subject_key,
        "value": json.loads(v.value_json),
        "derived_from": json.loads(v.derived_from),
        "valid_from": v.valid_from,
        "valid_to": v.valid_to,
        "last_confirmed_at": v.last_confirmed_at,
    }


def _close_active_views(db, scope: str) -> None:  # noqa: ANN001 —— SQLAlchemy Session
    for v in db.scalars(
        select(MemoryView).where(
            MemoryView.view_type == VIEW_TYPE,
            MemoryView.subject_key == scope,
            MemoryView.valid_to.is_(None),
        )
    ).all():
        v.valid_to = _now_iso()


def refresh_view(scope: str, *, confirmed_at: str | None = None) -> dict[str, Any] | None:
    """从该 scope 的活跃 assertion 重新派生 view（旧活跃版本 valid_to 关闭）。

    无任何 assertion 的 scope 返回 None 且不落 view——与 rebuild 的重放范围
    （只重放有 assertion 的 scope）保持一致，否则重建一致性会被空 scope 破坏。
    """
    actives = ledger.active_assertions(scope)
    if not actives:
        return None
    constraints: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    for e in actives:
        c = e["content"]
        field = c.get("field")
        if field is None or c.get("value") is None:  # value=None = 已撤销
            continue
        constraints[field] = c["value"]
        provenance[field] = {
            "event_id": e["id"],
            "turn": c.get("turn"),
            "confidence": e["confidence"],
            "source": e["source"],
        }
    now = confirmed_at or _now_iso()
    with SessionLocal() as db:
        _close_active_views(db, scope)
        view = MemoryView(
            view_type=VIEW_TYPE,
            subject_key=scope,
            value_json=json.dumps(
                {"constraints": constraints, "provenance": provenance}, ensure_ascii=False
            ),
            derived_from=json.dumps([e["id"] for e in actives]),
            valid_from=now,
            last_confirmed_at=now,
        )
        db.add(view)
        db.commit()
        return _view_dict(view)


def current_view(scope: str) -> dict[str, Any] | None:
    """该 scope 当前活跃的 hard_constraints view（recall_constraint 的数据源）。"""
    with SessionLocal() as db:
        v = db.scalars(
            select(MemoryView)
            .where(
                MemoryView.view_type == VIEW_TYPE,
                MemoryView.subject_key == scope,
                MemoryView.valid_to.is_(None),
            )
            .order_by(MemoryView.id.desc())
            .limit(1)
        ).first()
        return _view_dict(v) if v else None


def _active_views_snapshot() -> dict[tuple[str, str], dict[str, Any]]:
    """全部活跃 view 的语义快照（排除时间戳，用于重建一致性比较）。"""
    with SessionLocal() as db:
        views = db.scalars(
            select(MemoryView).where(MemoryView.valid_to.is_(None))
        ).all()
        return {
            (v.view_type, v.subject_key): {
                "value": json.loads(v.value_json),
                "derived_from": sorted(json.loads(v.derived_from)),
            }
            for v in views
        }


def rebuild_views() -> dict[str, Any]:
    """重放全量 Ledger 重建 Views（§7.2 定期重放）：关闭全部活跃 view → 逐 scope 重派生。

    consistent = 已有 view 的 scope 重建后值不变（§13「可回放 Ledger」验收口径）；
    「有 assertion 但从未建过 view」的 scope 会被补建（healed，自愈而非不一致）。
    """
    before = _active_views_snapshot()
    now = _now_iso()
    with SessionLocal() as db:
        for v in db.scalars(
            select(MemoryView).where(MemoryView.valid_to.is_(None))
        ).all():
            v.valid_to = now
        db.commit()
    # 只重放出现过 assertion 的 scope，与增量路径（apply → refresh）范围一致
    scopes = sorted({e["scope"] for e in ledger.list_events(event_type="assertion")})
    for s in scopes:
        refresh_view(s)
    after = _active_views_snapshot()
    healed = sorted(f"{k[0]}:{k[1]}" for k in after.keys() - before.keys())
    consistent = all(after.get(k) == v for k, v in before.items())
    return {
        "consistent": consistent,
        "healed": healed,
        "scopes": scopes,
        "before": {f"{k[0]}:{k[1]}": v for k, v in before.items()},
        "after": {f"{k[0]}:{k[1]}": v for k, v in after.items()},
    }
