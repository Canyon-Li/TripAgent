"""API 契约测试（TASKS 1.4 / 1.5，§15 契约层）：schema、trace 贯穿、不可行处理。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _base_req(**kw) -> dict:
    base = dict(
        city="beijing",
        days=3,
        hotel="wangfujing",
        budget=10_000,
        daily_minutes_cap=480,
        must_visit=["forbidden_city"],
        preferences=["history", "food"],
        session_id="s-001",
        start_date="2026-11-18",
    )
    base.update(kw)
    return base


def test_plan_example_request_feasible(client: TestClient) -> None:
    """§10 请求示例 → 200 + 结构完整（TASKS 验收的接口侧）。"""
    r = client.post("/api/plan", json=_base_req())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible"
    assert body["trace_id"].startswith("tr-")
    assert body["applied_constraints"]["budget"] == 10_000
    assert body["applied_constraints"]["source"] == "request"
    assert body["verification"]["violations"] == []
    assert body["verification"]["recomputed_budget"] > 0
    assert len(body["days"]) == 3
    for day in body["days"]:
        assert day["date_index"] >= 0
        assert day["away_minutes"] <= 480
        for stop in day["stops"]:
            assert stop["poi"] and stop["name"]
            assert len(stop["arrive"]) == 5 and ":" in stop["arrive"]
            assert stop["wait_min"] >= 0
    # 必去点覆盖
    all_pois = {s["poi"] for d in body["days"] for s in d["stops"]}
    assert "forbidden_city" in all_pois


def test_plan_persists_run_and_spans(client: TestClient) -> None:
    """trace_id 贯穿 runs / spans（阶段 1 验收：GET /api/runs/{trace_id} 完整可查）。"""
    r = client.post("/api/plan", json=_base_req(session_id="s-trace"))
    trace_id = r.json()["trace_id"]

    g = client.get(f"/api/runs/{trace_id}")
    assert g.status_code == 200
    run = g.json()
    assert run["trace_id"] == trace_id
    assert run["session_id"] == "s-trace"
    assert run["status"] == "succeeded"
    assert run["git_sha"]
    assert run["request"]["budget"] == 10_000
    names = [s["name"] for s in run["spans"]]
    assert "solver.solve" in names and "verifier.check" in names
    for span in run["spans"]:
        assert span["end_ms"] >= span["start_ms"]
        assert span["status"] == "ok"
    solver_span = next(s for s in run["spans"] if s["name"] == "solver.solve")
    assert solver_span["meta"]["objective"] > 0


def test_plan_infeasible_returns_reasons_no_days(client: TestClient) -> None:
    """预算 20 元装不下故宫门票 60 元 → infeasible + 明确原因 + 不硬凑行程。"""
    r = client.post("/api/plan", json=_base_req(
        days=1, budget=20, must_visit=["forbidden_city"], start_date="2026-11-18",
    ))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["days"] == []
    assert any("must_visit_not_covered" in reason for reason in body["infeasible_reasons"])


def test_plan_infeasible_closed_weekday(client: TestClient) -> None:
    """2026-11-23 周一：故宫闭馆，1 日行程必去故宫 → 不可行原因要说明。"""
    r = client.post("/api/plan", json=_base_req(
        days=1, budget=5_000, must_visit=["forbidden_city"], start_date="2026-11-23",
    ))
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["days"] == []
    assert body["infeasible_reasons"]


def test_plan_rejects_unknown_poi(client: TestClient) -> None:
    r = client.post("/api/plan", json=_base_req(must_visit=["not_a_poi"]))
    assert r.status_code == 422
    assert "unknown" in r.json()["detail"]


def test_plan_rejects_unsupported_city(client: TestClient) -> None:
    r = client.post("/api/plan", json=_base_req(city="shanghai"))
    assert r.status_code == 422


def test_plan_rejects_bad_days_budget(client: TestClient) -> None:
    assert client.post("/api/plan", json=_base_req(days=0)).status_code == 422
    assert client.post("/api/plan", json=_base_req(budget=-1)).status_code == 422


def test_runs_404_for_unknown_trace(client: TestClient) -> None:
    assert client.get("/api/runs/tr-nonexistent").status_code == 404


def test_plan_costs_reported(client: TestClient) -> None:
    r = client.post("/api/plan", json=_base_req())
    cost = r.json()["cost"]
    assert cost["latency_ms"] >= 0
    assert cost["token_in"] == 0 and cost["token_out"] == 0  # 阶段 1 无 LLM 参与 /api/plan
