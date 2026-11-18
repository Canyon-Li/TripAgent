"""POST /api/plan 与 GET /api/runs/{trace_id}（§10 / TASKS 1.4、1.5）。

主链路（阶段 1 版，§7.1 的确定性部分）：
    请求校验 → 数据加载(只读) → 求解 → 独立校验 → runs/spans 落库
求解+校验段落自阶段 2 起抽取到 runtime/pipeline.py，与 /api/chat 共用
（LLM 只在 chat 两端出现，此处保持纯结构化入口，便于评测，ADR-001）。
不可行路径（1.5）：求解无解 / 必去未覆盖 / 校验冲突 → status=infeasible +
infeasible_reasons，**不返回硬凑的行程**（days 为空）。
错误路径（阶段 4，§7.5/§15）：能力不可用 → 503 + run 落 failed；
其他异常 → 500 结构化错误体（code/retryable/message），run 同样落 failed。
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.db import runs as runs_db
from app.db.repository import DataRepository, get_repository
from app.registry import service as registry_service
from app.runtime.pipeline import run_plan_pipeline, solver_result_to_claims  # noqa: F401 (re-export)
from app.schemas import AppliedConstraintsOut, CostOut, PlanRequest, PlanResponse

router = APIRouter(prefix="/api")


def _validate_against_dataset(req: PlanRequest, repo: DataRepository) -> None:
    """数据集相关的语义校验（pydantic 管不到的部分）。"""
    unknown = [p for p in req.must_visit if p not in {poi.id for poi in repo.pois}]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"msg": "must_visit 含数据集中不存在的 POI", "unknown": unknown},
        )


@router.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    t0 = time.perf_counter()
    settings = get_settings()

    trace_id = f"tr-{uuid.uuid4().hex[:12]}"
    req.start_date = req.start_date or dt.date.today()

    runs_db.create_run(
        trace_id=trace_id,
        session_id=req.session_id,
        request_json=req.model_dump_json(),
        git_sha=settings.git_sha,
    )

    def _fail_run() -> int:
        latency = int((time.perf_counter() - t0) * 1000)
        runs_db.finish_run(trace_id, "failed", latency)
        return latency

    # 统一错误模型（§7.5 / §15）：任何失败 run 落 failed + 结构化错误体，不硬凑行程
    try:
        repo = get_repository()
        _validate_against_dataset(req, repo)
        response = run_plan_pipeline(repo=repo, req=req, trace_id=trace_id)
    except HTTPException:
        _fail_run()
        raise
    except registry_service.CapabilityUnavailableError as e:
        _fail_run()
        raise HTTPException(
            status_code=503,
            detail={"code": "capability_unavailable", "retryable": True, "message": str(e)},
        ) from e
    except Exception as e:  # noqa: BLE001 —— 统一兜底：矩阵加载失败 / 求解崩溃等
        _fail_run()
        raise HTTPException(
            status_code=500,
            detail={"code": "internal_error", "retryable": False, "message": str(e)},
        ) from e

    latency_ms = int((time.perf_counter() - t0) * 1000)
    response.applied_constraints = AppliedConstraintsOut(budget=req.budget)
    response.cost = CostOut(latency_ms=latency_ms)
    runs_db.save_response(trace_id, response.model_dump(mode="json"))
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
