"""runs / spans 读写（§9）：trace_id 贯穿一次 /api/plan 的全部阶段。"""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Run, Span


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def create_run(trace_id: str, session_id: str, request_json: str, git_sha: str) -> None:
    with SessionLocal() as db:
        db.add(Run(
            trace_id=trace_id,
            session_id=session_id,
            request_json=request_json,
            status="running",
            git_sha=git_sha,
            started_at=_now_iso(),
        ))
        db.commit()


def save_response(trace_id: str, response: dict) -> None:
    """把最终 PlanResponse 落到 runs.response_json（最近一次可行方案回退 + Trace 回放）。"""
    with SessionLocal() as db:
        run = db.get(Run, trace_id)
        if run is None:
            return
        run.response_json = json.dumps(response, ensure_ascii=False)
        db.commit()


def get_last_feasible_run(session_id: str, exclude_trace_id: str | None = None) -> dict | None:
    """该 session 最近一次可行行程（§7.1「最近一次可行方案」降级数据源）。

    可行 = run succeeded 且 response_json 内 status == "feasible"；
    exclude_trace_id 用于排除当前正在处理的 run 自身。
    """
    with SessionLocal() as db:
        stmt = (
            select(Run)
            .where(Run.session_id == session_id)
            .where(Run.status == "succeeded")
            .where(Run.response_json.is_not(None))
            .order_by(Run.finished_at.desc().nulls_last(), Run.started_at.desc())
        )
        for run in db.scalars(stmt).all():
            if exclude_trace_id and run.trace_id == exclude_trace_id:
                continue
            try:
                resp = json.loads(run.response_json)
            except (TypeError, ValueError):
                continue
            if resp.get("status") == "feasible":
                return {"trace_id": run.trace_id, "response": resp}
    return None


def finish_run(
    trace_id: str,
    status: str,
    latency_ms: int,
    token_in: int = 0,
    token_out: int = 0,
    model: str | None = None,
) -> None:
    with SessionLocal() as db:
        run = db.get(Run, trace_id)
        if run is None:
            return
        run.status = status
        run.latency_ms = latency_ms
        run.token_in = token_in
        run.token_out = token_out
        if model:
            run.model = model
        run.finished_at = _now_iso()
        db.commit()


def add_span(
    trace_id: str,
    name: str,
    start_ms: int,
    end_ms: int,
    status: str,
    meta: dict | None = None,
) -> None:
    with SessionLocal() as db:
        db.add(Span(
            trace_id=trace_id,
            name=name,
            start_ms=start_ms,
            end_ms=end_ms,
            status=status,
            meta_json=json.dumps(meta, ensure_ascii=False) if meta else None,
        ))
        db.commit()


def get_run_with_spans(trace_id: str) -> dict | None:
    with SessionLocal() as db:
        run = db.get(Run, trace_id)
        if run is None:
            return None
        spans = db.scalars(
            select(Span).where(Span.trace_id == trace_id).order_by(Span.id)
        ).all()
        return {
            "trace_id": run.trace_id,
            "session_id": run.session_id,
            "status": run.status,
            "model": run.model,
            "git_sha": run.git_sha,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "latency_ms": run.latency_ms,
            "token_in": run.token_in,
            "token_out": run.token_out,
            "request": json.loads(run.request_json),
            "spans": [
                {
                    "id": s.id,
                    "name": s.name,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "duration_ms": s.end_ms - s.start_ms,
                    "status": s.status,
                    "meta": json.loads(s.meta_json) if s.meta_json else None,
                }
                for s in spans
            ],
        }
