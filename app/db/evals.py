"""eval_results 读写（§9）：runner 落库 + 看板按 git_sha 聚合趋势。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import EvalResult


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def save_eval_results(
    git_sha: str,
    case_id: str,
    metrics: list[tuple[str, bool, float | None]],
) -> None:
    """一次评测运行的某用例的全部指标各落一行（metric, passed, value）。"""
    now = _now_iso()
    with SessionLocal() as db:
        db.add_all(
            EvalResult(git_sha=git_sha, case_id=case_id, metric=metric,
                       passed=int(passed), value=value, created_at=now)
            for metric, passed, value in metrics
        )
        db.commit()


def list_metric_trend(metric: str, limit: int = 50) -> list[dict]:
    """单指标按 (git_sha, 评测时间) 的趋势序列——同 sha 多次运行取最后一次。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(EvalResult.git_sha, EvalResult.passed, EvalResult.value,
                   EvalResult.created_at)
            .where(EvalResult.metric == metric)
            .order_by(EvalResult.id)
        ).all()
    # 同 git_sha 聚一次运行：passed 计数 / value 求和（如 time_window_violations 求和、
    # pass_rate 类求和后由调用方除以用例数）
    runs: dict[str, dict] = {}
    for git_sha, passed, value, created_at in rows:
        r = runs.setdefault(git_sha, {"passed": 0, "total": 0, "value": 0.0,
                                      "created_at": created_at})
        r["passed"] += passed
        r["total"] += 1
        r["value"] += value or 0.0
        r["created_at"] = created_at
    trend = [
        {"git_sha": sha, **r} for sha, r in sorted(
            runs.items(), key=lambda kv: kv[1]["created_at"])
    ]
    return trend[-limit:]


def list_report_runs(limit: int = 50) -> list[dict]:
    """历史评测运行列表（按 created_at 倒序，一个 git_sha+时间桶 = 一次运行）。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(EvalResult.git_sha, EvalResult.metric, EvalResult.passed,
                   EvalResult.value, EvalResult.created_at)
            .order_by(EvalResult.id)
        ).all()
    runs: dict[tuple[str, str], dict] = {}
    for git_sha, metric, passed, value, created_at in rows:
        key = (git_sha, created_at[:19])  # 同一秒内批量写入的行归为同一次评测运行
        r = runs.setdefault(key, {
            "git_sha": git_sha, "created_at": created_at,
            "metrics": {}, "cases": set(),
        })
        m = r["metrics"].setdefault(metric, {"passed": 0, "total": 0, "value": 0.0})
        m["passed"] += passed
        m["total"] += 1
        m["value"] += value or 0.0
    out = []
    for r in sorted(runs.values(), key=lambda x: x["created_at"], reverse=True)[:limit]:
        out.append({
            "git_sha": r["git_sha"],
            "created_at": r["created_at"],
            "metrics": {
                k: {"pass_rate": v["passed"] / v["total"] if v["total"] else None,
                    "value_sum": v["value"]}
                for k, v in r["metrics"].items()
            },
        })
    return out


def metric_names() -> list[str]:
    """已落库的全部指标名（看板下拉用）。"""
    with SessionLocal() as db:
        return [n for n in db.scalars(
            select(EvalResult.metric).distinct().order_by(EvalResult.metric)
        ).all()]
