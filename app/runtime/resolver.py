"""Resolver（§7.6 / ADR-008 / B14 / B15）：白名单 function calling 意图解析 + 护栏 + 降级。

护栏（每次 tool_call 逐条执行，违规不中断、全部记录）：
- 非白名单工具名 → 拒绝并记违规；
- 参数 JSON 解析 + pydantic 强校验（tools.py 的 schema）；
- 单次请求工具调用上限 MAX_TOOL_CALLS=3（累计计数，防循环），超出截断并记违规。

B15 双通道（真实 function calling 回路）：模型调 recall_constraint(key) 时，
运行时执行 recall_lookup（同源 memory_views）并把结果以 tool 消息回灌给模型，
模型随后 submit_plan_request；召回值再与固定链路 Memory.read 合并去重
（合并动作在 chat 主链路，两路同源故正常无增量，链路完备性由测试守住）。

降级（§7.1 / §14）：LLM 不可用 / 失败 → 规则模板正则抽取（degraded=True，
进 spans 观测）。规则模板是兜底而非主路径——抽取能力有限（不支持中文数字等），
这正是「降级率」指标要观测的。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.llm.adapter import LLMAdapter, LLMError, LLMRetryableError, UsageMeter
from app.llm.tools import (
    MAX_TOOL_CALLS,
    PREFERENCE_KEYWORDS,
    TOOLS_SCHEMA,
    SubmitPlanRequestArgs,
    validate_tool_call,
)

# recall_lookup 返回：{"value": 检索值, "source": 如 "memory_views#7"}；未命中为 None
RecallLookup = Callable[[str], dict[str, Any] | None]

SYSTEM_PROMPT_TEMPLATE = """你是 TripAgent 的意图解析器（Resolver）。
用户正在多轮对话中规划北京行程。

当前已确认的约束（来自记忆层，权威，直接可用，无需再问）：
{constraints}

规则：
1. 从用户最新消息中抽取【本轮明确说出】的约束，调用 submit_plan_request——
   只包含本轮新出或变化的字段，用户没提的字段一律省略，严禁编造数值；
2. 用户显式引用历史约束（如“还是按上次那个预算”）时，调用
   recall_constraint(key=字段名)；
3. 关键信息（如预算）既不在本条消息中、也不在上面的已确认约束里时，
   调用 ask_clarification(field=..., reason=...)；
4. 不要输出自然语言，只能调用工具。
{catalog}"""


def _catalog_section(poi_catalog: str) -> str:
    if not poi_catalog:
        return ""
    return f"\n可选景点目录（must_visit 请使用下列 id）：\n{poi_catalog}"


@dataclass
class ResolverOutcome:
    kind: str                                  # plan | clarification
    fields: dict[str, Any] = field(default_factory=dict)   # kind=plan：本轮显式约束
    clarification: tuple[str, str] | None = None           # (field, reason)
    # B15 辅助通道实际召回：key -> {"value", "source"}（未命中进 recalled_misses）
    recalled: dict[str, dict[str, Any]] = field(default_factory=dict)
    recalled_misses: list[str] = field(default_factory=list)
    guardrail_violations: list[str] = field(default_factory=list)
    degraded: bool = False                     # True = 规则模板降级产出
    llm_used: bool = True
    note: str = ""


def build_resolver_messages(
    message: str,
    active_constraints: dict[str, Any],
    history: list[dict[str, str]],
    poi_catalog: str = "",
) -> list[dict[str, str]]:
    """system（含约束重述，§7.2）+ 历史 + 本轮消息。"""
    constraints = json.dumps(active_constraints, ensure_ascii=False, default=str)
    system = SYSTEM_PROMPT_TEMPLATE.format(
        constraints=constraints, catalog=_catalog_section(poi_catalog)
    )
    return [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": message},
    ]


def resolve(
    llm: LLMAdapter | None,
    *,
    message: str,
    active_constraints: dict[str, Any],
    history: list[dict[str, str]],
    meter: UsageMeter,
    llm_enabled: bool = True,
    poi_index: tuple[tuple[str, str], ...] = (),
    poi_catalog: str = "",
    recall_lookup: RecallLookup | None = None,
) -> ResolverOutcome:
    """意图解析主入口（function calling 回路，B15）。

    LLM 缺失/不可用/失败时降级规则模板（返回 degraded=True）。模型每轮可调
    白名单工具；recall_constraint 由运行时执行检索并把结果回灌，直到模型给出
    submit_plan_request / ask_clarification，或累计工具调用数达上限（防循环）。
    """
    if llm is None or not llm_enabled:
        meter.mark_degraded()
        return _degraded_outcome(message, poi_index)

    messages = build_resolver_messages(message, active_constraints, history, poi_catalog)
    violations: list[str] = []
    fields: dict[str, Any] = {}
    clarification: tuple[str, str] | None = None
    recalled: dict[str, dict[str, Any]] = {}
    recalled_misses: list[str] = []
    total_calls = 0

    while total_calls < MAX_TOOL_CALLS:
        try:
            result = llm.chat(messages=messages, tools=TOOLS_SCHEMA)
            meter.add(result)
        except (LLMError, LLMRetryableError):
            meter.mark_degraded()
            return _degraded_outcome(message, poi_index)

        calls = list(result.tool_calls)
        if not calls:
            break  # 模型放弃工具调用（纯文本）——按无有效调用处理
        if total_calls + len(calls) > MAX_TOOL_CALLS:
            violations.append(
                f"too_many_tool_calls:{total_calls + len(calls)}>{MAX_TOOL_CALLS}"
            )
            calls = calls[: MAX_TOOL_CALLS - total_calls]

        tool_replies: list[dict[str, str]] = []
        recall_assistant_calls: list[dict[str, Any]] = []
        for tc in calls:
            total_calls += 1
            args, violation = validate_tool_call(tc.name, tc.arguments_json)
            if violation:
                violations.append(violation)
                continue
            if tc.name == "submit_plan_request":
                a = args if isinstance(args, SubmitPlanRequestArgs) else SubmitPlanRequestArgs()
                # city 恒为 beijing、confidence 是观测信号：都不是约束字段，不进 fields
                fields = {
                    k: v for k, v in a.model_dump().items()
                    if v is not None and k not in ("city", "confidence")
                }
            elif tc.name == "ask_clarification":
                clarification = (args.field, args.reason)  # type: ignore[attr-defined]
            elif tc.name == "recall_constraint":
                key = args.key  # type: ignore[attr-defined]
                hit = recall_lookup(key) if recall_lookup else None
                if hit is not None:
                    recalled[key] = hit
                    content = {"key": key, "found": True, **hit}
                else:
                    recalled_misses.append(key)
                    content = {"key": key, "found": False,
                               "note": "记忆视图（memory_views）中无该字段"}
                # 回灌：assistant 声明 + tool 结果，模型下一轮据此 submit/ask（B15）
                recall_assistant_calls.append({
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments_json},
                })
                tool_replies.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(content, ensure_ascii=False, default=str),
                })

        if fields or clarification:
            break  # 终态：拿到可执行约束或澄清请求
        if tool_replies and total_calls < MAX_TOOL_CALLS:
            messages = [
                *messages,
                {"role": "assistant", "content": result.text or "",
                 "tool_calls": recall_assistant_calls},
                *tool_replies,
            ]
            continue
        if not tool_replies:
            break  # 无进展（违规或空轮）→ 终止
        # recall 回灌额度耗尽仍未终态：循环护栏
        violations.append(f"tool_call_budget_exhausted:{total_calls}")

    if fields:  # 拿到可执行约束即规划（模型同时 ask 时以 submit 为准）
        return ResolverOutcome(
            kind="plan", fields=fields, recalled=recalled,
            recalled_misses=recalled_misses, guardrail_violations=violations,
        )
    if clarification:
        return ResolverOutcome(
            kind="clarification", clarification=clarification, recalled=recalled,
            recalled_misses=recalled_misses, guardrail_violations=violations,
        )
    return ResolverOutcome(
        kind="clarification",
        clarification=("message", "未能从消息中解析出行程约束，请说明天数、预算或想去的景点"),
        recalled=recalled, recalled_misses=recalled_misses,
        guardrail_violations=[*violations, "no_effective_tool_call"],
    )


# ---------------- 规则模板降级（§7.1） ----------------

_BUDGET_RE = re.compile(
    r"(?:预算|花费|最多花|不超过)[^\d]{0,4}(\d+(?:\.\d+)?)\s*(万)?"  # “预算 5000” “预算1万”
    r"|(\d+(?:\.\d+)?)\s*万(?:元|块)?\s*(?:以内|内|预算)?"
    r"|(\d+(?:\.\d+)?)\s*(?:元|块)(?:以内|内)?"
)
_DAYS_RE = re.compile(r"(\d+)\s*(?:天|日)")
_CAP_RE = re.compile(r"每天[^\d]{0,6}(\d{1,3})\s*(?:小时|h)")


def rule_template_parse(message: str, poi_index: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    """正则抽取显式约束（LLM 降级路径；能力有限是预期内的，进降级率观测）。"""
    fields: dict[str, Any] = {}
    m = _BUDGET_RE.search(message)
    if m:
        if m.group(1):  # “预算 N(万)?”
            val = float(m.group(1)) * (10000 if m.group(2) else 1)
        elif m.group(3):  # “N 万”
            val = float(m.group(3)) * 10000
        else:  # “N 元”
            val = float(m.group(4))
        fields["budget"] = val
    m = _DAYS_RE.search(message)
    if m and 1 <= int(m.group(1)) <= 7:
        fields["days"] = int(m.group(1))
    m = _CAP_RE.search(message)
    if m:
        cap = int(m.group(1)) * 60
        if 60 <= cap <= 720:
            fields["daily_minutes_cap"] = cap
    must = [pid for pid, name in poi_index if name in message]
    if must:
        fields["must_visit"] = must
    prefs = [cat for cat, words in PREFERENCE_KEYWORDS.items() if any(w in message for w in words)]
    if prefs:
        fields["preferences"] = prefs
    return fields


def _degraded_outcome(message: str, poi_index: tuple[tuple[str, str], ...]) -> ResolverOutcome:
    fields = rule_template_parse(message, poi_index)
    if fields:
        return ResolverOutcome(
            kind="plan", fields=fields, degraded=True, llm_used=False,
            note="LLM 不可用，规则模板降级解析（§7.1）",
        )
    return ResolverOutcome(
        kind="clarification",
        clarification=(
            "message",
            "LLM 暂不可用且规则模板未能解析该消息，请说明：几天、预算多少、想去哪",
        ),
        degraded=True, llm_used=False,
        guardrail_violations=["llm_degraded_rule_template"],
        note="LLM 不可用，规则模板降级解析（§7.1）",
    )
