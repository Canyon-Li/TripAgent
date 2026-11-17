"""3.4「最近一次可行方案」降级（§7.1 尾项收口）：

LLM 失败 → 规则模板降级后（解析失败 / 重规划不可行）→ 回退本 session 最近一次可行行程；
无历史可行方案时维持澄清，非降级的澄清不受影响。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.api.chat import _get_llm_optional
from app.llm.adapter import LLMRetryableError
from tests.fakes import FakeLLM, tool_call_result


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if not lines or not lines[0].startswith("event: "):
            continue
        events.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return events


@pytest.fixture()
def install_llm(client: TestClient) -> Callable[[FakeLLM], None]:
    from app.main import app

    def _install(fake: FakeLLM) -> None:
        app.dependency_overrides[_get_llm_optional] = lambda: fake

    yield _install
    app.dependency_overrides.pop(_get_llm_optional, None)


def _post(client: TestClient, message: str, *, session: str, user: str):
    r = client.post("/api/chat", json={
        "session_id": session, "user_id": user, "message": message,
    })
    assert r.status_code == 200
    return _parse_sse(r.text)


def _seed_feasible_turn(install_llm, client, *, session: str, user: str) -> dict:
    """第 1 轮正常出行程（留下 runs.response_json 可行方案）。"""
    install_llm(FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"budget": 9000, "days": 1, "must_visit": ["forbidden_city"]}))),
        tool_call_result(text="已排好 1 天故宫行程。"),  # noqa: F841 —— responder 调用
    ]))
    events = _post(client, "预算9000玩1天必去故宫", session=session, user=user)
    done = events[-1][1]
    assert done["type"] == "plan" and done["plan"]["status"] == "feasible"
    return done


def test_fallback_when_rule_template_parse_fails(install_llm, client):
    """LLM 失败 + 规则模板解析不出约束 + 有历史可行方案 → 回退而非澄清。"""
    first = _seed_feasible_turn(install_llm, client, session="s-fb1", user="u-fb1")

    install_llm(FakeLLM([LLMRetryableError("down"), LLMRetryableError("down")]))
    events = _post(client, "就按之前说的那样安排吧", session="s-fb1", user="u-fb1")
    done = events[-1][1]
    assert done["type"] == "plan"
    assert done["fallback"] == {"type": "last_feasible", "trace_id": first["trace_id"]}
    assert "降级" in done["reply"] and first["trace_id"] in done["reply"]
    # 回放行程挂在当前 trace 下且内容可行
    assert done["plan"]["status"] == "feasible" and done["plan"]["days"]
    assert done["plan"]["trace_id"] == done["trace_id"] != first["trace_id"]
    # spans：responder.render 记录 fallback 来源；run succeeded；response 已落库
    run = client.get(f"/api/runs/{done['trace_id']}").json()
    render = next(s for s in run["spans"] if s["name"] == "responder.render")
    assert render["meta"]["fallback_trace_id"] == first["trace_id"]
    assert run["status"] == "succeeded"


def test_fallback_when_degraded_replan_infeasible(install_llm, client):
    """LLM 失败 + 规则模板解析出约束但重规划不可行 → 回退并附不可行原因。"""
    _seed_feasible_turn(install_llm, client, session="s-fb2", user="u-fb2")

    install_llm(FakeLLM([LLMRetryableError("down"), LLMRetryableError("down")]))
    events = _post(client, "预算1元玩1天必去故宫", session="s-fb2", user="u-fb2")
    done = events[-1][1]
    assert done["type"] == "plan" and done["fallback"]["type"] == "last_feasible"
    assert "不可行" in done["reply"]


def test_no_history_keeps_clarification(install_llm, client):
    """无历史可行方案：LLM 失败 + 解析失败 → 维持澄清（不编造）。"""
    install_llm(FakeLLM([LLMRetryableError("down"), LLMRetryableError("down")]))
    events = _post(client, "就按之前说的那样安排吧", session="s-fb3", user="u-fb3")
    done = events[-1][1]
    assert done["type"] == "clarification"
    assert "fallback" not in done


def test_non_degraded_clarification_not_replaced_by_fallback(install_llm, client):
    """LLM 正常主动澄清：即使有历史可行方案也不回退（非降级路径）。"""
    _seed_feasible_turn(install_llm, client, session="s-fb4", user="u-fb4")
    install_llm(FakeLLM([
        tool_call_result(("ask_clarification",
                          json.dumps({"field": "budget", "reason": "预算多少"}))),
    ]))
    events = _post(client, "帮我规划一下", session="s-fb4", user="u-fb4")
    assert events[-1][1]["type"] == "clarification"
