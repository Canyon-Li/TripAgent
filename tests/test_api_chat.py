"""2.7 /api/chat：SSE 契约、多轮约束演进、applied_constraints 回显、spans 完整、降级。"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.api.chat import _get_llm_optional
from tests.fakes import FakeLLM, tool_call_result


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if not lines or not lines[0].startswith("event: "):
            continue
        event = lines[0][len("event: "):]
        data = json.loads(lines[1][len("data: "):])
        events.append((event, data))
    return events


@pytest.fixture()
def install_llm(client: TestClient) -> Callable[[FakeLLM], None]:
    """FastAPI 官方注入：dependency_overrides 按依赖函数对象键替换。

    （monkeypatch.setattr 换模块属性对已注册的 Depends 无效——路由持有原函数引用。）
    """
    from app.main import app

    def _install(fake: FakeLLM) -> None:
        app.dependency_overrides[_get_llm_optional] = lambda: fake

    yield _install
    app.dependency_overrides.pop(_get_llm_optional, None)


def _post(client: TestClient, message: str, *, session: str, user: str):
    """session 与 user 必须显式传入：user 级记忆跨测试泄漏会改变分支走向。"""
    r = client.post("/api/chat", json={
        "session_id": session, "user_id": user, "message": message,
    })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return _parse_sse(r.text)


def test_sse_event_sequence_and_done_payload(install_llm, client):
    from app.llm.adapter import LLMResult
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"days": 2, "budget": 8000, "must_visit": ["forbidden_city"]}))),
        LLMResult(text="已规划 2 天行程。校验通过。", token_in=10, token_out=5, model="fake"),
    ])
    install_llm(fake)
    events = _post(client, "预算8000玩2天，必去故宫", session="sc-1", user="u-t1")
    names = [e for e, _ in events]
    assert names[0] == "start"
    assert "stage" in names and "delta" in names
    assert names[-1] == "done"
    done = events[-1][1]
    assert done["type"] == "plan" and done["turn"] == 1
    plan = done["plan"]
    assert plan["status"] == "feasible" and len(plan["days"]) == 2
    # applied_constraints 逐字段回显：budget 来源是 request、turn=1
    ac = plan["applied_constraints"]
    assert ac["budget"] == 8000 and ac["source"] == "request"
    by_field = {c["field"]: c for c in ac["constraints"]}
    assert by_field["budget"]["source"] == "request"
    assert by_field["budget"]["turn"] == 1
    # LLM token 计量贯穿
    assert done["llm"]["token_in"] >= 10 and done["llm"]["llm_calls"] == 2


def test_clarification_when_budget_missing_everywhere(install_llm, client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"days": 2}))),
    ])
    install_llm(fake)
    events = _post(client, "想去北京玩两天", session="sc-cl", user="u-t2")
    done = events[-1][1]
    assert done["type"] == "clarification" and done["field"] == "budget"
    # 不落 solver span（不硬凑）
    run = client.get(f"/api/runs/{done['trace_id']}").json()
    assert "solver.solve" not in [s["name"] for s in run["spans"]]


def test_resolver_ask_clarification_passthrough(install_llm, client):
    fake = FakeLLM([
        tool_call_result(("ask_clarification",
                          json.dumps({"field": "budget", "reason": "请问预算多少"}))),
    ])
    install_llm(fake)
    events = _post(client, "帮我规划行程", session="sc-ask", user="u-t3")
    assert events[-1][1]["type"] == "clarification"
    assert events[-1][1]["reason"] == "请问预算多少"


def test_spans_complete_for_plan_turn(install_llm, client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 10000, "days": 1}))),
    ])
    install_llm(fake)
    events = _post(client, "预算1万玩1天", session="sc-span", user="u-t4")
    trace_id = events[-1][1]["trace_id"]
    run = client.get(f"/api/runs/{trace_id}").json()
    names = [s["name"] for s in run["spans"]]
    for expected in ("memory.read", "resolver.resolve", "memory.write",
                     "solver.solve", "verifier.check", "responder.render"):
        assert expected in names
    assert run["status"] == "succeeded" and run["token_in"] >= 0


def test_llm_failure_degrades_to_rule_template(install_llm, client):
    from app.llm.adapter import LLMRetryableError
    fake = FakeLLM([LLMRetryableError("down"), LLMRetryableError("down")])
    install_llm(fake)
    events = _post(client, "预算9000玩1天必去故宫", session="sc-deg", user="u-t5")
    done = events[-1][1]
    assert done["type"] == "plan"
    assert done["llm"]["degraded"] is True
    assert done["plan"]["status"] == "feasible"  # 规则模板抽取仍出行程
    resolver_stage = next(d for e, d in events if e == "stage" and d.get("name") == "resolver")
    assert resolver_stage["degraded"] is True


def test_unknown_poi_name_mapped_and_unknown_dropped(install_llm, client):
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"budget": 10000, "days": 1, "must_visit": ["故宫", "不存在的地方"]}))),
    ])
    install_llm(fake)
    events = _post(client, "必去故宫", session="sc-norm", user="u-t6")
    done = events[-1][1]
    stage = next(d for e, d in events if e == "stage" and d.get("name") == "resolver")
    assert "unknown_poi_dropped:不存在的地方" in stage["guardrail_violations"]
    pois = {s["poi"] for d in done["plan"]["days"] for s in d["stops"]}
    assert "forbidden_city" in pois
