"""2.7 /api/chat：SSE 契约、多轮约束演进、applied_constraints 回显、spans 完整、降级。"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from conftest import parse_sse as _parse_sse
from fastapi.testclient import TestClient

from app.api.chat import get_llm_optional
from tests.fakes import FakeLLM, tool_call_result


@pytest.fixture()
def install_llm(client: TestClient) -> Callable[[FakeLLM], None]:
    """FastAPI 官方注入：dependency_overrides 按依赖函数对象键替换。

    （monkeypatch.setattr 换模块属性对已注册的 Depends 无效——路由持有原函数引用。）
    """
    from app.main import app

    def _install(fake: FakeLLM) -> None:
        app.dependency_overrides[get_llm_optional] = lambda: fake

    yield _install
    app.dependency_overrides.pop(get_llm_optional, None)


def _post(client: TestClient, message: str, *, session: str, user: str,
          start_date: str = "2026-11-18"):
    """session 与 user 必须显式传入：user 级记忆跨测试泄漏会改变分支走向。

    start_date 固定周三：故宫等周一闭馆，缺省锚 date.today() 会让断言随星期几漂移
    （audit 2026-11-23 P1；与 eval_cases.jsonl 同口径）。
    """
    r = client.post("/api/chat", json={
        "session_id": session, "user_id": user, "message": message,
        "start_date": start_date,
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


def test_chinese_preference_normalized_to_category_id(install_llm, client):
    """真实 DeepSeek 实测发现：模型常输出中文类目（“历史”）而非 id——须归一化。"""
    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"budget": 10000, "days": 1, "preferences": ["历史", "美食", "不存在的类目"]}))),
    ])
    install_llm(fake)
    events = _post(client, "喜欢历史和美食", session="sc-pref", user="u-t7")
    done = events[-1][1]
    by_field = {c["field"]: c for c in done["plan"]["applied_constraints"]["constraints"]}
    assert by_field["preferences"]["value"] == ["history", "food"]
    stage = next(d for e, d in events if e == "stage" and d.get("name") == "resolver")
    assert "unknown_preference_dropped:不存在的类目" in stage["guardrail_violations"]


def test_b15_recall_channel_end_to_end(install_llm, client):
    """B15 双通道：模型显式 recall(budget) → 运行时从 memory_views 同源检索回灌
    → 模型 submit；done.memory.recalled 携带检索值；applied budget 与权威通道去重一致。"""
    install_llm(FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 6600, "days": 1}))),
    ]))
    _post(client, "预算6600玩1天", session="sc-b15", user="u-b15")

    install_llm(FakeLLM([
        tool_call_result(("recall_constraint", json.dumps({"key": "budget"}))),
        tool_call_result(("submit_plan_request", json.dumps({"days": 2}))),
    ]))
    events = _post(client, "还是按上次那个预算，玩2天", session="sc-b15", user="u-b15")
    done = events[-1][1]
    recalled = done["memory"]["recalled"]
    assert recalled["budget"]["value"] == 6600
    assert recalled["budget"]["source"].startswith("memory_views#")
    assert done["memory"]["recalled_misses"] == []
    # 两路同源去重：applied 与权威 Memory.read 注入一致
    ac = done["plan"]["applied_constraints"]
    assert ac["budget"] == 6600 and ac["source"] == recalled["budget"]["source"]
    # 两次 LLM 调用（recall 轮 + submit 轮）+ responder
    assert done["llm"]["llm_calls"] == 3


def test_sse_error_event_and_failed_run(monkeypatch, install_llm, client):
    """修 2：主链路异常 → SSE error 事件 + run 落 failed（不再永停 running）。"""
    install_llm(FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps({"budget": 5000, "days": 1}))),
    ]))

    def boom(**_kw):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr("app.api.chat.run_plan_pipeline", boom)
    events = _post(client, "预算5000玩1天", session="sc-err", user="u-err")
    assert events[-1][0] == "error"
    assert "solver exploded" in events[-1][1]["detail"]
    run = client.get(f"/api/runs/{events[0][1]['trace_id']}").json()
    assert run["status"] == "failed"
