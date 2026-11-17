"""2.8 记忆回放 API + 跨会话用户隔离（B9）：user 级约束跨 session 注入、用户不串扰。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.chat import _get_llm_optional
from tests.fakes import FakeLLM, tool_call_result


@pytest.fixture()
def install_llm(client: TestClient):
    from app.main import app

    def _install(fake: FakeLLM) -> None:
        app.dependency_overrides[_get_llm_optional] = lambda: fake

    yield _install
    app.dependency_overrides.pop(_get_llm_optional, None)


def _chat(client: TestClient, message: str, *, session: str, user: str, fake: FakeLLM) -> dict:
    """单轮 chat，返回 done 事件 payload（fake 每轮重装以注入脚本）。"""
    from app.main import app

    app.dependency_overrides[_get_llm_optional] = lambda: fake
    r = client.post("/api/chat", json={
        "session_id": session, "user_id": user, "message": message,
    })
    app.dependency_overrides.pop(_get_llm_optional, None)
    assert r.status_code == 200
    done_line = [ln for ln in r.text.split("\n") if ln.startswith("data: ")][-1]
    return json.loads(done_line[len("data: "):])


def test_cross_session_memory_injection_b9(client):
    """u-alice 在 session A 说预算 → 新 session B（同 user）首轮仍注入该预算。"""
    # 会话 A 第 1 轮：定预算和天数
    fake_a = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"budget": 6000, "days": 2, "must_visit": ["forbidden_city"]}))),
    ])
    done_a = _chat(client, "预算6000玩2天必去故宫", session="mA", user="u-alice", fake=fake_a)
    assert done_a["type"] == "plan" and done_a["plan"]["status"] == "feasible"

    # 会话 B（同 user，全新 session）：只说天数——预算必须来自 user 级记忆
    fake_b = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"days": 1}))),
    ])
    done_b = _chat(client, "这次只玩1天", session="mB", user="u-alice", fake=fake_b)
    assert done_b["type"] == "plan"
    ac = done_b["plan"]["applied_constraints"]
    assert ac["budget"] == 6000
    assert ac["source"].startswith("memory_views#")  # 来源回显指向视图
    by_field = {c["field"]: c for c in ac["constraints"]}
    assert by_field["budget"]["turn"] == 1  # 首次提出轮次可追溯
    assert by_field["budget"]["source"].startswith("memory_views#")
    # 会话 A 的 session 级约束（days/must_visit）不泄漏进会话 B
    assert "days" in by_field and by_field["days"]["source"] == "request"
    assert "must_visit" not in by_field or by_field["must_visit"]["source"] == "request"


def test_user_isolation_b9(client):
    """u-alice 的预算不给 u-bob——bob 缺预算应触发澄清而非借用。"""
    fake_a = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 6000, "days": 2}))),
    ])
    _chat(client, "预算6000玩2天", session="iso-a", user="u-alice", fake=fake_a)
    fake_b = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"days": 2}))),
    ])
    done_b = _chat(client, "玩2天", session="iso-b", user="u-bob", fake=fake_b)
    assert done_b["type"] == "clarification" and done_b["field"] == "budget"


def test_memory_events_endpoint_replays_ledger(client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 5000, "days": 1}))),
    ])
    _chat(client, "预算5000玩1天", session="api-ev", user="u-ev", fake=fake)
    r = client.get("/api/memory/events", params={"session_id": "api-ev"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2  # observation + assertions
    types = [e["event_type"] for e in body["events"]]
    assert "observation" in types and "assertion" in types
    # 追加序（id 递增）
    ids = [e["id"] for e in body["events"]]
    assert ids == sorted(ids)
    # 无过滤条件拒绝
    assert "error" in client.get("/api/memory/events").json()


def test_memory_views_endpoint_shows_scope_chain(client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 7777, "days": 1}))),
    ])
    _chat(client, "预算7777玩1天", session="api-vw", user="u-vw", fake=fake)
    r = client.get("/api/memory/views", params={"session_id": "api-vw", "user_id": "u-vw"})
    assert r.status_code == 200
    v = r.json()["views"]
    assert v["session:api-vw"]["value"]["constraints"].get("days") == 1
    assert v["user:u-vw"]["value"]["constraints"].get("budget") == 7777
    # global 可能含其他测试留下的约束（global 全局共享），只断言不含本测试字段
    g = (v["global"] or {}).get("value", {}).get("constraints", {})
    assert "budget" not in g and "days" not in g


def test_memory_rebuild_endpoint_consistent(client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 8888, "days": 1}))),
    ])
    _chat(client, "预算8888玩1天", session="api-rb", user="u-rb", fake=fake)
    r = client.post("/api/memory/rebuild")
    assert r.status_code == 200
    body = r.json()
    assert body["consistent"] is True
    assert "session:api-rb" in body["scopes"]
    # 重建后预算仍在（视图被正确重建）
    v = client.get("/api/memory/views", params={
        "session_id": "api-rb", "user_id": "u-rb"}).json()["views"]
    assert v["user:u-rb"]["value"]["constraints"]["budget"] == 8888
