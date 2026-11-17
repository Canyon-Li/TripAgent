"""评测 API（§10）：POST /api/eval/run 触发全量评测、GET /api/eval/reports 趋势与列表。

POST /run 进程内同步执行（复用当前 DATABASE_URL；评测 runs/spans 与线上同库，trace 可查）。
默认公开集 + current 列——无 LLM key 时 chat 用例自动走脚本替身（确定性），
有 key 时用真实 LLM。含 hidden / A / B 列由请求体指定（真实评测口径，§14）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import evals as evals_db
from app.eval import report as report_mod
from app.eval import runner

router = APIRouter(prefix="/api/eval")


class EvalRunRequest(BaseModel):
    include_hidden: bool = False
    baselines: list[Literal["current", "A", "B"]] = Field(default=["current"])


@router.post("/run")
def run_eval(req: EvalRunRequest) -> dict:
    settings = get_settings()
    result = runner.run_eval(
        settings.eval_cases_path,
        baselines=tuple(req.baselines) or ("current",),
        include_hidden=req.include_hidden,
        llm_mode="auto",
    )
    current = result["columns"].get("current", [])
    return {
        "git_sha": result["git_sha"],
        "eval_set": result["meta"],
        "report_path": result["report_path"],
        "summary": result["summary"],
        "passed": sum(1 for c in current if c["passed"]),
        "total": len(current),
        "aggregates": result["aggregates"].get("current", {}),
    }


@router.get("/reports")
def list_reports(metric: str | None = None, limit: int = 20) -> dict:
    """历史评测趋势：每指标的 (git_sha, 时间, 聚合) 序列 + 运行列表（看板数据源）。

    metrics_meta 下发指标的中文标签与聚合形态（rate/sum/mean）——前端下拉与
    图表不再各自硬编码指标语义。
    """
    names = evals_db.metric_names()
    if metric is None:
        wanted = names
    else:
        wanted = [metric] if metric in names else []
    return {
        "metrics": names,
        "metrics_meta": [m for m in report_mod.metrics_meta() if m["name"] in names],
        "trends": {m: evals_db.list_metric_trend(m) for m in wanted},
        "runs": evals_db.list_report_runs(limit=limit),
        "baseline": _baseline_summary(),
    }


def _baseline_summary() -> dict | None:
    """CI 基线摘要（看板展示当前基线值；缺失返回 None）。"""
    import json

    path = report_mod.BASELINE_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"git_sha": data.get("git_sha"),
                "eval_set_version": data.get("eval_set_version"),
                "metrics": data.get("metrics", {})}
    except ValueError:
        return None
