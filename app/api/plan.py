"""POST /api/plan 与 GET /api/runs/{trace_id}（§10 / TASKS 1.4、1.5）。

主链路（阶段 1 版，§7.1 的确定性部分）：
    请求校验 → 数据加载(只读) → Solver.solve → 独立 Verifier.check → runs/spans 落库
不可行路径（1.5）：求解无解 / 必去未覆盖 / 校验冲突 → status=infeasible +
infeasible_reasons，**不返回硬凑的行程**（days 为空）。
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.db import runs as runs_db
from app.db.repository import DataRepository, format_hhmm, get_repository
from app.schemas import (
    AppliedConstraintsOut,
    CostOut,
    DayOut,
    PlanRequest,
    PlanResponse,
    SolverOut,
    StopOut,
    VerificationOut,
    ViolationOut,
)
from app.solver.routing import SolveRequest, solve_plan
from app.verifier.verify import DayClaim, StopClaim, VerifyRequest, verify_itinerary

router = APIRouter(prefix="/api")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _validate_against_dataset(req: PlanRequest, repo: DataRepository) -> None:
    """数据集相关的语义校验（pydantic 管不到的部分）。"""
    unknown = [p for p in req.must_visit if p not in {poi.id for poi in repo.pois}]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"msg": "must_visit 含数据集中不存在的 POI", "unknown": unknown},
        )


def solver_result_to_claims(days: list) -> list[DayClaim]:  # noqa: ANN001 —— 求解器 DayPlan
    """求解结果 → 校验器声明（组合层适配器；校验器本身不依赖求解器，ADR-001）。"""
    return [
        DayClaim(
            date_index=d.date_index,
            depart_min=d.depart_min,
            stops=tuple(StopClaim(s.poi_id, s.arrive_min, s.leave_min) for s in d.stops),
        )
        for d in days
    ]


@router.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    t0 = time.perf_counter()
    settings = get_settings()
    repo = get_repository()
    _validate_against_dataset(req, repo)

    trace_id = f"tr-{uuid.uuid4().hex[:12]}"
    start_date = req.start_date or dt.date.today()
    solve_req = SolveRequest(
        days=req.days,
        budget=req.budget,
        daily_minutes_cap=req.daily_minutes_cap,
        must_visit=frozenset(req.must_visit),
        preferences=frozenset(req.preferences),
        start_date=start_date,
    )

    runs_db.create_run(
        trace_id=trace_id,
        session_id=req.session_id,
        request_json=req.model_dump_json(),
        git_sha=settings.git_sha,
    )

    # ---- 求解 ----
    s0 = _now_ms()
    result = solve_plan(repo, solve_req)
    runs_db.add_span(
        trace_id, "solver.solve", s0, _now_ms(), "ok",
        meta={
            "solver_status": result.solver_status,
            "objective": result.objective,
            "phases": result.phases,
            "visited": len(result.visited),
            "dropped_must_visit": result.dropped_must_visit,
        },
    )

    infeasible_reasons: list[str] = []
    if result.status == "no_solution":
        infeasible_reasons.append(f"solver_no_solution: {result.solver_status}")
    elif result.dropped_must_visit:
        infeasible_reasons += [
            f"must_visit_not_covered: {p}" for p in result.dropped_must_visit
        ]

    days_out: list[DayOut] = []
    verification = VerificationOut()

    if not infeasible_reasons:
        # ---- 独立校验（ADR-001：不复用求解器代码路径）----
        v0 = _now_ms()
        verify_req = VerifyRequest(
            days=req.days,
            budget=req.budget,
            daily_minutes_cap=req.daily_minutes_cap,
            must_visit=solve_req.must_visit,
            start_date=start_date,
        )
        verification_raw = verify_itinerary(
            repo, verify_req, solver_result_to_claims(result.days)
        )
        runs_db.add_span(
            trace_id, "verifier.check", v0, _now_ms(), "ok",
            meta={
                "violations": len(verification_raw.violations),
                "recomputed_budget": verification_raw.recomputed_budget,
            },
        )
        verification = VerificationOut(
            violations=[
                ViolationOut(
                    type=v.type, detail=v.detail, day_index=v.day_index, poi=v.poi
                )
                for v in verification_raw.violations
            ],
            recomputed_budget=verification_raw.recomputed_budget,
        )
        if verification_raw.violations:
            infeasible_reasons += [
                f"verification_failed[{v.type}]: {v.detail}"
                for v in verification_raw.violations
            ]
        else:
            days_out = [
                DayOut(
                    date_index=d.date_index,
                    date=d.date,
                    depart=format_hhmm(d.depart_min),
                    return_=format_hhmm(d.return_min),
                    away_minutes=d.away_minutes,
                    stops=[
                        StopOut(
                            poi=s.poi_id,
                            name=repo.poi(s.poi_id).name,
                            category=repo.poi(s.poi_id).category,
                            arrive=format_hhmm(s.arrive_min),
                            leave=format_hhmm(s.leave_min),
                            wait_min=s.wait_min,
                            ticket=repo.poi(s.poi_id).ticket,
                            note=repo.poi(s.poi_id).note,
                        )
                        for s in d.stops
                    ],
                )
                for d in result.days
            ]

    latency_ms = int((time.perf_counter() - t0) * 1000)
    response = PlanResponse(
        trace_id=trace_id,
        status="feasible" if not infeasible_reasons else "infeasible",
        city=req.city,
        days=days_out,
        applied_constraints=AppliedConstraintsOut(budget=req.budget),
        verification=verification,
        infeasible_reasons=infeasible_reasons,
        cost=CostOut(latency_ms=latency_ms),
        solver=SolverOut(
            status=result.solver_status,
            objective=result.objective,
            wall_ms=result.wall_ms,
            phases=result.phases,
        ),
    )
    runs_db.finish_run(trace_id, "succeeded", latency_ms)
    return response


@router.get("/runs/{trace_id}")
def get_run(trace_id: str) -> dict:
    run = runs_db.get_run_with_spans(trace_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {trace_id}")
    return run


@router.get("/pois")
def list_pois() -> list[dict]:
    """POI 目录（§10 之外的只读辅助端点，供前端必去点选择器使用）。"""
    repo = get_repository()
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "ticket": p.ticket,
            "duration_min": p.duration_min,
            "score": p.score,
            "open": p.open,
            "close": p.close,
        }
        for p in repo.pois
    ]
