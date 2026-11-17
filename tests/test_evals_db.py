"""3.1/3.5：eval_results 落库与聚合、runs.response_json / get_last_feasible_run、span 计时。"""

from __future__ import annotations

import time

from app.db import evals as evals_db
from app.db import runs as runs_db
from app.runtime.pipeline import span_bounds, tick


def _mk_run(trace_id: str, session_id: str = "s-eval-db", status: str = "succeeded",
            response: dict | None = None) -> None:
    runs_db.create_run(trace_id, session_id, "{}", "test")
    if response is not None:
        runs_db.save_response(trace_id, response)
    runs_db.finish_run(trace_id, status, 10)


def test_get_last_feasible_run_picks_latest_and_skips_infeasible():
    _mk_run("tr-a", response={"status": "infeasible", "days": []})
    time.sleep(0.01)
    _mk_run("tr-b", response={"status": "feasible", "days": [{"date_index": 0}]})
    time.sleep(0.01)
    _mk_run("tr-c", response={"status": "feasible", "days": [{"date_index": 0}]})
    time.sleep(0.01)
    _mk_run("tr-d", status="failed")  # 无 response：不算可行

    hit = runs_db.get_last_feasible_run("s-eval-db")
    assert hit is not None and hit["trace_id"] == "tr-c"
    assert hit["response"]["status"] == "feasible"

    # 排除自身后取次新
    hit2 = runs_db.get_last_feasible_run("s-eval-db", exclude_trace_id="tr-c")
    assert hit2["trace_id"] == "tr-b"
    # 其他 session 隔离
    assert runs_db.get_last_feasible_run("s-other") is None


def test_eval_results_save_and_trend():
    evals_db.save_eval_results("sha1", "case_a", [
        ("hard_constraint_pass_rate", True, 1.0),
        ("time_window_violations", True, 0.0),
    ])
    evals_db.save_eval_results("sha1", "case_b", [
        ("hard_constraint_pass_rate", False, 0.0),
        ("time_window_violations", False, 2.0),
    ])
    evals_db.save_eval_results("sha2", "case_a", [
        ("hard_constraint_pass_rate", True, 1.0),
    ])

    trend = evals_db.list_metric_trend("hard_constraint_pass_rate")
    mine = [t for t in trend if t["git_sha"].startswith("sha")]  # 排除其他测试写入的行
    assert [t["git_sha"] for t in mine] == ["sha1", "sha2"]
    assert mine[0]["passed"] == 1 and mine[0]["total"] == 2
    tw = evals_db.list_metric_trend("time_window_violations")
    assert next(t for t in tw if t["git_sha"] == "sha1")["value"] == 2.0  # 求和语义

    reports = evals_db.list_report_runs()
    shas = [r["git_sha"] for r in reports]
    assert "sha1" in shas and "sha2" in shas
    r1 = next(r for r in reports if r["git_sha"] == "sha1")
    assert abs(r1["metrics"]["hard_constraint_pass_rate"]["pass_rate"] - 0.5) < 1e-9
    assert set(evals_db.metric_names()) >= {"hard_constraint_pass_rate"}


def test_span_bounds_precise_duration():
    """3.5：墙钟锚点 + perf_counter 时长——短于墙钟粒度的 span 也不为 0（Windows ~15.6ms）。"""
    t = tick()
    time.sleep(0.002)  # 2ms：远小于 Windows 墙钟粒度
    start_ms, end_ms = span_bounds(t)
    assert end_ms >= start_ms
    assert end_ms - start_ms >= 1  # perf_counter 精度兜住亚毫秒段
