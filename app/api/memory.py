"""记忆回放 API（§13「可回放 Ledger」演示口径 / TASKS 2.8）。

- GET /api/memory/events：按 session/user/scope 过滤的 Ledger 事件流（追加序）；
- GET /api/memory/views：scope 链（session→user→global）当前活跃 hard_constraints view；
- POST /api/memory/rebuild：重放全量 Ledger 重建 Views，返回一致性结果。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.memory import ledger, views

router = APIRouter(prefix="/api/memory")


@router.get("/events")
def memory_events(
    session_id: str | None = None,
    user_id: str | None = None,
    scope: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict:
    """Ledger 事件回放（须给至少一个过滤条件，避免全库倾倒）。"""
    if not (session_id or user_id or scope):
        return {"error": "provide at least one of session_id / user_id / scope"}
    events = ledger.list_events(
        session_id=session_id, user_id=user_id, scope=scope, limit=limit
    )
    return {"count": len(events), "events": events}


@router.get("/views")
def memory_views(session_id: str, user_id: str) -> dict:
    """scope 链三个作用域的当前活跃视图（Memory.read 的同源数据，B15）。"""
    chain = [f"session:{session_id}", f"user:{user_id}", "global"]
    return {"views": {scope: views.current_view(scope) for scope in chain}}


@router.post("/rebuild")
def memory_rebuild() -> dict:
    """重放 Ledger 重建 Views；consistent=True 为 §13 验收口径。"""
    return views.rebuild_views()
