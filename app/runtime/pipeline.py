"""共用规划管线：/api/plan 与 /api/chat 共享的「Registry 路由 → 求解 → 独立校验 → 组装」段落。

从 api/plan.py 原地抽取（行为不变，阶段 1 测试保持绿），使 chat 主链路
（§7.1）与结构化入口走完全相同的确定性路径——LLM 只在两端（Resolver/Responder），
中间不碰事实（ADR-001）。阶段 4 起求解经 Registry 选版本（ADR-007 灰度载体）。
"""

from __future__ import annotations

import datetime as dt
import time

from app.db import runs as runs_db
from app.db.repository import DataRepository, format_hhmm
from app.registry import service as registry
from app.registry.seed import SOLVER_VERSIONS
from app.schemas import (
    AppliedConstraintsOut,
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

# span 计时：墙钟锚点 + perf_counter 精确时长（Windows 墙钟 ~15.6ms 粒度会把短 span 记成 0ms，
# 瀑布图（§7.3）需要亚毫秒精度；schema 的 start_ms/end_ms 语义不变）
Tick = tuple[int, float]


def tick() -> Tick:
    return int(time.time() * 1000), time.perf_counter()


def span_bounds(t: Tick) -> tuple[int, int]:
    start_ms, pc0 = t
    return start_ms, start_ms + int((time.perf_counter() - pc0) * 1000)


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


def run_plan_pipeline(
    *,
    repo: DataRepository,
    req: PlanRequest,
    trace_id: str,
) -> PlanResponse:
    """结构化 PlanRequest → 求解 → 独立校验 → PlanResponse（含 spans 落库）。

    不可行路径（TASKS 1.5）：no_solution / 必去未覆盖 / 校验冲突 → infeasible
    + infeasible_reasons，days 恒为空，不硬凑行程。cost 由调用方按全链路耗时填。
    """
    # start_date 局部收窄：/api/plan 已填充默认值，chat 侧可能未填（类型上可空）
    start_date = req.start_date or dt.date.today()

    solve_req = SolveRequest(
        days=req.days,
        budget=req.budget,
        daily_minutes_cap=req.daily_minutes_cap,
        must_visit=frozenset(req.must_visit),
        preferences=frozenset(req.preferences),
        start_date=start_date,
    )

    # ---- Registry 路由（§7.1：Memory.read 之后、Solver.solve 之前）----
    # solver 按 traffic_weight 加权选版本分发参数（ADR-007 灰度载体）；
    # matrix / verifier 单版本进程内能力，路由结果作为可用性闸门（摘除即 503 不硬凑）。
    # 按 name 而非 kind 闸门：kind 桶会被同桶无关卡冒名顶替（如再注册一张 tool 卡
    # 即掩盖 matrix 摘除）。
    r0 = tick()
    try:
        solver_card = registry.route_name_required("solver", trace_id)
        matrix_card = registry.route_name_required("matrix", trace_id)
        verifier_card = registry.route_name_required("verifier", trace_id)
        params = SOLVER_VERSIONS.get(solver_card.version)
        if params is None:  # 有卡无参数绑定：拒绝路由（与心跳口径一致）
            raise registry.CapabilityUnavailableError(
                f"solver@{solver_card.version} 无参数绑定（SOLVER_VERSIONS 未收录）"
            )
    except registry.CapabilityUnavailableError as e:
        runs_db.add_span(
            trace_id, "registry.route", *span_bounds(r0), "error",
            meta={"error": str(e)},
        )
        raise
    runs_db.add_span(
        trace_id, "registry.route", *span_bounds(r0), "ok",
        meta={
            "solver": solver_card.ref,
            "matrix": matrix_card.ref,
            "verifier": verifier_card.ref,
        },
    )

    # ---- 求解 ----
    s0 = tick()
    result = solve_plan(repo, solve_req, params)
    runs_db.add_span(
        trace_id, "solver.solve", *span_bounds(s0), "ok",
        meta={
            "solver_version": solver_card.version,
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
        v0 = tick()
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
            trace_id, "verifier.check", *span_bounds(v0), "ok",
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
                    # pydantic alias 字段（return 是关键字，序列化别名），mypy 无插件视角不识别
                    return_=format_hhmm(d.return_min),  # type: ignore[call-arg]
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

    return PlanResponse(
        trace_id=trace_id,
        status="feasible" if not infeasible_reasons else "infeasible",
        city=req.city,
        days=days_out,
        applied_constraints=AppliedConstraintsOut(budget=req.budget),  # 调用方按来源覆写
        verification=verification,
        infeasible_reasons=infeasible_reasons,
        solver=SolverOut(
            status=result.solver_status,
            objective=result.objective,
            wall_ms=result.wall_ms,
            phases=result.phases,
        ),
    )
