"""/api/registry/* 契约（§10）：查询 / upsert / 窄更新（权重、注销）/ 心跳状态。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.registry import service

_DEFAULT_REFS = {"solver@v1", "solver@v2", "matrix@static", "verifier@v1",
                 "llm_adapter@deepseek"}


@pytest.fixture(autouse=True)
def _restore_weights(default_registry_state):
    yield


def test_get_capabilities_lists_defaults(client: TestClient):
    r = client.get("/api/registry/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["capabilities"])
    refs = {c["name"] + "@" + c["version"] for c in body["capabilities"]}
    assert _DEFAULT_REFS <= refs
    card = next(c for c in body["capabilities"] if c["name"] + "@" + c["version"] == "solver@v2")
    assert card["kind"] == "solver" and card["status"] == "active"


def test_get_capabilities_filter_by_kind(client: TestClient):
    r = client.get("/api/registry/capabilities", params={"kind": "verifier"})
    refs = {c["name"] for c in r.json()["capabilities"]}
    assert refs == {"verifier"}


def test_get_capabilities_tag_and_latest_params(client: TestClient):
    r = client.get("/api/registry/capabilities", params={"tag": "score-first"})
    assert {c["name"] + "@" + c["version"] for c in r.json()["capabilities"]} == {"solver@v2"}
    r = client.get("/api/registry/capabilities", params={"latest": "true"})
    by_name = {c["name"]: c["version"] for c in r.json()["capabilities"]}
    assert by_name["solver"] == "v2" and by_name["matrix"] == "static"


def test_get_capabilities_carries_policy_view(client: TestClient):
    r = client.get("/api/registry/capabilities", params={"kind": "solver"})
    policies = {p["name"]: p for p in r.json()["policies"]}
    assert policies["solver"]["policy"] == "pinned"  # 默认 100/0 = pinned 到 v1
    assert policies["solver"]["latest"] == "v2"


def test_post_upsert_is_the_rollback_channel(client: TestClient):
    """POST 整卡 upsert 改权重 → 下一次 /api/plan 生效（回滚通道，§7.4）。

    POST 体用当前库里的真实卡（只改 weight）——整卡 upsert 会覆写全部字段，
    用占位 schema 会破坏后续 input_schema 发现类断言。
    """
    current = service.get_card("solver", "v2").model_dump()
    current["traffic_weight"] = 100
    r = client.post("/api/registry/capabilities", json=current)
    assert r.status_code == 200
    assert service.route("solver", "tr-api-1").version == "v2"  # 权重全在 v2


def test_weight_endpoint_updates_one_row(client: TestClient):
    r = client.post("/api/registry/capabilities/solver/v2/weight", json={"weight": 0})
    assert r.status_code == 200 and r.json()["traffic_weight"] == 0
    assert service.get_card("solver", "v2").traffic_weight == 0
    # 不存在的卡 404；负权重 422
    assert client.post("/api/registry/capabilities/solver/v9/weight",
                       json={"weight": 1}).status_code == 404
    assert client.post("/api/registry/capabilities/solver/v1/weight",
                       json={"weight": -1}).status_code == 422


def test_deregister_endpoint_archives(client: TestClient):
    r = client.post("/api/registry/capabilities/matrix/static/deregister")
    assert r.status_code == 200 and r.json()["status"] == "retired"
    assert client.post("/api/registry/capabilities/matrix/v9/deregister").status_code == 404
    service.set_status("matrix", "static", "active")  # 恢复


def test_health_endpoint_reports_heartbeat(client: TestClient):
    r = client.get("/api/registry/health")
    assert r.status_code == 200
    body = r.json()
    assert body["interval_s"] > 0
    caps = body["capabilities"]
    # 首跳（lifespan）已跑过：默认卡全部有确定结果（llm_adapter 检查的是配置在场，
    # 测试环境的哨兵 key 也是「已配置」——真实不可用场景由故障注入测试覆盖）
    for ref in _DEFAULT_REFS:
        assert caps[ref]["ok"] is True and caps[ref]["checked_at"], ref
    assert body["summary"]["healthy"] >= len(_DEFAULT_REFS)
