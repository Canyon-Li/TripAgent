"""故障注入测试（§15）：LLM 超时降级、矩阵能力不可用、心跳摘除与回切、interrupted 清扫。

口径：降级路径可用、不硬凑行程、run 状态如实落库（failed / interrupted）。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_llm_optional
from app.db import runs as runs_db
from app.registry import health as registry_health
from app.registry import service
from tests.fakes import FakeLLM


@pytest.fixture()
def install_llm(client: TestClient) -> Callable[[FakeLLM], None]:
    from app.main import app

    def _install(fake: FakeLLM) -> None:
        app.dependency_overrides[get_llm_optional] = lambda: fake

    yield _install
    app.dependency_overrides.pop(get_llm_optional, None)


@pytest.fixture(autouse=True)
def _restore_registry(default_registry_state):
    """注入后恢复：session 级 client 与 DB 被后续测试复用，状态必须归位。"""
    yield


_PLAN_REQ = {
    "city": "beijing", "days": 1, "hotel": "wangfujing", "budget": 5000,
    "daily_minutes_cap": 480, "must_visit": [], "preferences": [], "session_id": "fi",
}


def _last_trace() -> str:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import Run

    with SessionLocal() as db:
        return db.scalars(select(Run.trace_id).order_by(Run.started_at.desc())).first()


def test_llm_timeout_degrades_to_rule_template(install_llm, client: TestClient):
    """LLM 超时/不可达：Resolver 降级规则模板，chat 主链路仍产出可行行程（§7.5）。"""
    from app.llm.adapter import LLMRetryableError

    install_llm(FakeLLM([LLMRetryableError("timeout after 30s"), LLMRetryableError("timeout")]))
    resp = client.post("/api/chat", json={
        "message": "预算9000玩1天必去故宫", "user_id": "u-fi", "session_id": "s-fi-timeout",
    })
    done = json.loads(next(
        b.split("data: ", 1)[1] for b in resp.text.split("\n\n") if b.startswith("event: done")
    ))
    assert done["type"] == "plan" and done["plan"]["status"] == "feasible"
    assert done["llm"]["degraded"] is True  # 全程降级但未硬凑


def test_matrix_capability_unavailable_returns_503(client: TestClient):
    """矩阵能力摘除：readyz 503 + /api/plan 503 结构化错误 + run 落 failed、不硬凑。"""
    service.set_status("matrix", "static", "unhealthy")

    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert "matrix" in ready.json()["checks"]["registry"]  # 按 name 报告（与闸门同口径）

    r = client.post("/api/plan", json=_PLAN_REQ)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "capability_unavailable" and detail["retryable"] is True

    run = runs_db.get_run_with_spans(_last_trace())
    assert run["status"] == "failed" and run["request"]["session_id"] == "fi"
    assert run["spans"][-1]["name"] == "registry.route" and run["spans"][-1]["status"] == "error"


def test_repo_failure_maps_to_structured_500(monkeypatch, client: TestClient):
    """矩阵服务不可用（数据层异常形态）：统一错误模型 internal_error + run failed。"""

    def boom():
        raise RuntimeError("matrix.json corrupted")

    monkeypatch.setattr("app.api.plan.get_repository", boom)
    r = client.post("/api/plan", json=_PLAN_REQ)
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["code"] == "internal_error" and detail["retryable"] is False
    assert "corrupted" in detail["message"]
    assert runs_db.get_run_with_spans(_last_trace())["status"] == "failed"


def _raise_crash() -> None:
    raise RuntimeError("v1 crashed")


def test_solver_v1_heartbeat_failure_fails_over_to_v2(monkeypatch, client: TestClient):
    """心跳失败自动摘除：v1 摘除后流量全落 v2；绑定恢复 + 心跳通过后自动回切（§7.4）。"""
    # 按卡内 healthcheck.type 分发（动态卡同路径）：工厂对 v1 产出崩溃检查
    monkeypatch.setitem(
        registry_health.HEALTHCHECK_BINDINGS, "self_test_solve",
        lambda card: _raise_crash if card.version == "v1" else (lambda: None))
    service.set_weight("solver", "v1", 100)
    service.set_weight("solver", "v2", 100)

    results = registry_health.run_heartbeat()
    assert results["solver@v1"]["ok"] is False
    assert service.get_card("solver", "v1").status == "unhealthy"
    assert {service.route("solver", f"tr-fi-{i}").version for i in range(20)} == {"v2"}
    # /api/plan 走 v2 仍可行（独立校验器双保险，不硬凑）
    r = client.post("/api/plan", json=_PLAN_REQ)
    assert r.status_code == 200 and r.json()["status"] == "feasible"
    spans = runs_db.get_run_with_spans(r.json()["trace_id"])["spans"]
    solve_meta = next(s for s in spans if s["name"] == "solver.solve")["meta"]
    assert solve_meta["solver_version"] == "v2"

    # 绑定恢复 → 心跳通过 → 自动回切 active（retired 不复活，另由 API 测试覆盖）
    monkeypatch.setitem(
        registry_health.HEALTHCHECK_BINDINGS, "self_test_solve", registry_health.solver_check)
    registry_health.run_heartbeat()
    assert service.get_card("solver", "v1").status == "active"
    assert "v1" in {service.route("solver", f"tr-fi2-{i}").version for i in range(20)}


def test_all_solvers_removed_degrades_to_503(client: TestClient):
    service.set_status("solver", "v1", "unhealthy")
    service.set_status("solver", "v2", "unhealthy")
    r = client.post("/api/plan", json=_PLAN_REQ)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "capability_unavailable"


def test_stale_running_runs_swept_to_interrupted():
    """启动清扫（§7.1 优雅关闭）：遗留 running run → interrupted。"""
    runs_db.create_run("tr-stale-1", "s-fi", "{}", "test")
    runs_db.create_run("tr-stale-2", "s-fi", "{}", "test")
    runs_db.finish_run("tr-stale-2", "succeeded", 5)
    swept = runs_db.mark_stale_running_interrupted()
    assert swept >= 1
    assert runs_db.get_run_with_spans("tr-stale-1")["status"] == "interrupted"
    assert runs_db.get_run_with_spans("tr-stale-2")["status"] == "succeeded"


def test_clarification_unaffected_by_registry(client: TestClient):
    """澄清路径不触求解（无 registry 依赖）：摘除 solver 不影响缺预算澄清。"""
    service.set_status("solver", "v1", "unhealthy")
    service.set_status("solver", "v2", "unhealthy")
    resp = client.post("/api/chat", json={
        "message": "想去北京玩", "user_id": "u-fi2", "session_id": "s-fi-clar",
    })
    assert "event: clarification" in resp.text
