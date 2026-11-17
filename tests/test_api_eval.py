"""3.7 评测 API 契约：POST /api/eval/run、GET /api/eval/reports（§10）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import evals as evals_db


def test_eval_run_endpoint(client: TestClient, monkeypatch):
    """run 端点契约：透传 baselines/include_hidden，返回报告路径与聚合摘要。"""
    captured: dict = {}

    def fake_run_eval(cases_path, *, baselines, include_hidden, llm_mode):  # noqa: ANN001
        captured.update(path=str(cases_path), baselines=baselines,
                        include_hidden=include_hidden, llm_mode=llm_mode)
        return {
            "meta": {"version": "t.1", "count": 2, "public": 2, "hidden": 0},
            "git_sha": "sha-x",
            "report_path": "reports/eval-sha-x.md",
            "columns": {"current": [
                {"case_id": "a", "passed": True, "failures": [], "hidden": False},
                {"case_id": "b", "passed": False, "failures": ["x"], "hidden": False},
            ]},
            "aggregates": {"current": {"final_pass_rate": 0.5}},
            "summary": "current: 1/2",
        }

    monkeypatch.setattr("app.api.eval.runner.run_eval", fake_run_eval)
    r = client.post("/api/eval/run", json={"include_hidden": True,
                                           "baselines": ["current", "B"]})
    assert r.status_code == 200
    body = r.json()
    assert body["git_sha"] == "sha-x"
    assert body["report_path"].endswith("eval-sha-x.md")
    assert body["passed"] == 1 and body["total"] == 2
    assert body["aggregates"]["final_pass_rate"] == 0.5
    assert captured["include_hidden"] is True
    assert captured["baselines"] == ("current", "B")

    # 默认值：current 列、不含 hidden、auto 模式
    r2 = client.post("/api/eval/run", json={})
    assert r2.status_code == 200
    assert captured["baselines"] == ("current",) and captured["include_hidden"] is False


def test_eval_reports_endpoint(client: TestClient):
    """reports 端点：趋势（git_sha 序列）+ 运行列表 + 基线摘要。"""
    evals_db.save_eval_results("sha-r1", "case_a", [("final_pass_rate", True, 1.0)])
    evals_db.save_eval_results("sha-r1", "case_b", [("final_pass_rate", False, 0.0)])
    evals_db.save_eval_results("sha-r2", "case_a", [("final_pass_rate", True, 1.0)])

    r = client.get("/api/eval/reports")
    assert r.status_code == 200
    body = r.json()
    assert "final_pass_rate" in body["metrics"]
    trend = body["trends"]["final_pass_rate"]
    by_sha = {t["git_sha"]: t for t in trend}
    assert by_sha["sha-r1"]["passed"] == 1 and by_sha["sha-r1"]["total"] == 2
    assert any(run["git_sha"] in ("sha-r1", "sha-r2") for run in body["runs"])
    assert body["baseline"] is None or "metrics" in body["baseline"]

    # metric 过滤
    r2 = client.get("/api/eval/reports", params={"metric": "final_pass_rate"})
    assert set(r2.json()["trends"]) == {"final_pass_rate"}
    # 未知 metric → 空趋势
    r3 = client.get("/api/eval/reports", params={"metric": "nope"})
    assert r3.json()["trends"] == {}


def test_eval_run_bad_baseline_rejected(client: TestClient):
    r = client.post("/api/eval/run", json={"baselines": ["Z"]})
    assert r.status_code == 422
