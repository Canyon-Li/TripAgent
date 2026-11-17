"""2.6 Resolver：白名单 function calling、护栏（非白名单/坏参数/超限）、规则模板降级。"""

from __future__ import annotations

import json

import pytest

from app.llm import adapter as llm_adapter
from app.llm.tools import TOOLS_SCHEMA, WHITELIST, validate_tool_call
from app.runtime.resolver import (
    build_resolver_messages,
    resolve,
    rule_template_parse,
)
from tests.fakes import FakeLLM, tool_call_result

POIS = (("forbidden_city", "故宫"), ("summer_palace", "颐和园"))


def _meter() -> llm_adapter.UsageMeter:
    return llm_adapter.UsageMeter()


def test_whitelist_exactly_three_tools_schema_names():
    """B14：恰好 3 个白名单工具，schema 名与白名单一致。"""
    names = {t["function"]["name"] for t in TOOLS_SCHEMA}
    assert names == WHITELIST == {"submit_plan_request", "ask_clarification", "recall_constraint"}


def test_resolve_normal_plan_path():
    fake = FakeLLM([tool_call_result(
        ("submit_plan_request", json.dumps({"days": 3, "budget": 8000, "must_visit": ["故宫"]})),
    )])
    out = resolve(fake, message="玩3天，预算8000，必去故宫",
                  active_constraints={}, history=[], meter=_meter())
    assert out.kind == "plan"
    assert out.fields == {"days": 3, "budget": 8000.0, "must_visit": ["故宫"]}
    assert out.guardrail_violations == [] and not out.degraded


def test_resolve_omits_none_fields_and_city():
    fake = FakeLLM([tool_call_result(
        ("submit_plan_request", json.dumps({"city": "beijing", "budget": 5000})),
    )])
    out = resolve(fake, message="预算5000", active_constraints={}, history=[], meter=_meter())
    assert out.fields == {"budget": 5000.0}  # city 剔除、None 字段不出现


def test_non_whitelist_tool_rejected_and_recorded():
    fake = FakeLLM([tool_call_result(
        ("submit_plan_request", json.dumps({"days": 2})),
        ("solve_itinerary_directly", '{"hack": true}'),  # 非白名单
    )])
    out = resolve(fake, message="x", active_constraints={}, history=[], meter=_meter())
    assert out.kind == "plan" and out.fields == {"days": 2}
    assert out.guardrail_violations == ["non_whitelist_tool:solve_itinerary_directly"]


def test_invalid_json_and_schema_violations_recorded():
    fake = FakeLLM([tool_call_result(
        ("submit_plan_request", "{not json"),
        ("submit_plan_request", json.dumps({"days": 99})),  # 越界（>7）
        ("ask_clarification", json.dumps({"field": "budget", "reason": "需要预算"})),
    )])
    out = resolve(fake, message="x", active_constraints={}, history=[], meter=_meter())
    assert out.kind == "clarification"
    assert out.clarification == ("budget", "需要预算")
    assert len(out.guardrail_violations) == 2
    assert out.guardrail_violations[0].startswith("invalid_arguments_json")
    assert out.guardrail_violations[1].startswith("schema_validation_failed")


def test_tool_call_limit_truncated():
    five = [("recall_constraint", json.dumps({"key": f"budget{i}"})) for i in range(5)]
    fake = FakeLLM([tool_call_result(*five)])
    out = resolve(fake, message="x", active_constraints={}, history=[], meter=_meter())
    assert "too_many_tool_calls:5>3" in out.guardrail_violations
    assert len(out.recalled_keys) == 3  # 截断到上限


def test_recall_constraint_recorded():
    fake = FakeLLM([tool_call_result(
        ("recall_constraint", json.dumps({"key": "budget"})),
        ("submit_plan_request", json.dumps({"days": 2})),
    )])
    out = resolve(fake, message="还是按上次那个预算，玩2天",
                  active_constraints={}, history=[], meter=_meter())
    assert out.kind == "plan" and out.recalled_keys == ["budget"]


def test_no_tool_call_falls_to_clarification():
    fake = FakeLLM([llm_adapter.LLMResult(text="你好呀")])
    out = resolve(fake, message="你好", active_constraints={}, history=[], meter=_meter())
    assert out.kind == "clarification"
    assert "no_effective_tool_call" in out.guardrail_violations


def test_llm_failure_degrades_to_rule_template():
    fake = FakeLLM([llm_adapter.LLMRetryableError("down")])
    out = resolve(fake, message="预算8000玩3天必去故宫",
                  active_constraints={}, history=[], meter=_meter(), poi_index=POIS)
    assert out.degraded and not out.llm_used
    assert out.fields["budget"] == 8000.0
    assert out.fields["days"] == 3
    assert out.fields["must_visit"] == ["forbidden_city"]


def test_llm_disabled_degrades_without_calling_adapter():
    fake = FakeLLM([])
    out = resolve(fake, message="预算8000玩3天", active_constraints={}, history=[],
                  meter=_meter(), llm_enabled=False, poi_index=POIS)
    assert out.degraded and not out.llm_used and fake.calls == []


def test_degraded_unparseable_becomes_clarification():
    out = resolve(FakeLLM([]), message="今天天气怎么样",
                  active_constraints={}, history=[], meter=_meter(), llm_enabled=False,
                  poi_index=POIS)
    assert out.kind == "clarification" and out.degraded


@pytest.mark.parametrize("msg,expect", [
    ("预算5000", {"budget": 5000.0}),
    ("预算1万", {"budget": 10000.0}),
    ("最多花8000元", {"budget": 8000.0}),
    ("5000元以内", {"budget": 5000.0}),
    ("玩3天", {"days": 3}),
    ("预算 3000 玩 2 天", {"budget": 3000.0, "days": 2}),
    ("每天在外不超过8小时", {"daily_minutes_cap": 480}),
    ("想去颐和园和故宫", {"must_visit": ["forbidden_city", "summer_palace"]}),
    ("喜欢历史和美食", {"preferences": ["history", "food"]}),
    ("就随便聊聊", {}),
])
def test_rule_template_parse(msg, expect):
    got = rule_template_parse(msg, POIS)
    for k, v in expect.items():
        assert got.get(k) == v


def test_messages_include_constraint_restatement_and_history():
    msgs = build_resolver_messages(
        "改成2天", {"budget": 5000, "days": 3},
        history=[{"role": "user", "content": "预算5000玩3天"}],
    )
    assert msgs[0]["role"] == "system"
    assert '"budget": 5000' in msgs[0]["content"]  # 约束重述（§7.2）
    assert msgs[-1] == {"role": "user", "content": "改成2天"}
    assert msgs[1]["content"] == "预算5000玩3天"


def test_validate_tool_call_model_returned():
    args, violation = validate_tool_call(
        "ask_clarification", json.dumps({"field": "budget", "reason": "r"})
    )
    assert violation is None and args is not None
    _, v = validate_tool_call("execute_sql", "{}")
    assert v == "non_whitelist_tool:execute_sql"
