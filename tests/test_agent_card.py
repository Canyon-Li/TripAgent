"""只读 Agent Card 契约（§7.7 / §15 / ADR-009）。

§15 L617 口径：断言 skills[0].id、输入输出模式与 /api/plan 请求 schema 一致、
x-tripagent.scope == "discovery-only"、不存在 A2A task 端点。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas import PlanRequest, PlanResponse


def _card(client: TestClient) -> dict:
    r = client.get("/.well-known/agent.json")
    assert r.status_code == 200
    return r.json()


def test_card_identity_and_endpoint(client: TestClient):
    card = _card(client)
    assert card["name"] == "TripAgent"
    assert card["url"].endswith("/api/plan")
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False}
    assert card["defaultInputModes"] == ["application/json"]
    assert card["defaultOutputModes"] == ["application/json"]


def test_card_declares_single_plan_skill(client: TestClient):
    skills = _card(client)["skills"]
    assert len(skills) == 1
    assert skills[0]["id"] == "plan_itinerary"
    assert skills[0]["inputModes"] == ["application/json"]
    assert skills[0]["outputModes"] == ["application/json"]


def test_card_matches_plan_request_schema(client: TestClient):
    """卡片由 /api/plan 请求 schema 生成——字段必须与 PlanRequest 一致（§7.7）。"""
    fields = _card(client)["x-tripagent"]["requestFields"]
    assert fields == list(PlanRequest.model_fields)
    # 反向兜底：schema 变了卡片没变 → 此处失败（守住「生成」而非手抄）


def test_card_is_discovery_only_no_a2a_task(client: TestClient):
    card = _card(client)["x-tripagent"]
    assert card["scope"] == "discovery-only"
    assert card["a2aTaskEndpoint"] is None
    assert "POST /api/plan" in card["note"]


def test_no_a2a_task_endpoints_exposed(client: TestClient):
    """不实现 message/send / Task 状态机：卡内不声明，路由表里也不存在。"""
    card = _card(client)
    assert "message/send" not in card and card.get("capabilities", {}).get("tasks") is None
    for path in ("/message/send", "/a2a/tasks", "/.well-known/card.json"):
        assert client.post(path).status_code == 404


def test_card_version_tracks_app(client: TestClient):
    from app import __version__

    assert _card(client)["version"] == __version__


def test_openapi_consistency(client: TestClient):
    """卡片声明的入口指向真实存在的 /api/plan（OpenAPI 里有该路径与 schema）。"""
    spec = client.get("/openapi.json").json()
    assert "/api/plan" in spec["paths"]
    assert PlanRequest.__name__ in str(spec) and PlanResponse.__name__ in str(spec)
