"""Responder（§7.1 链路末端）：LLM 生成解释文本；失败降级规则模板。

LLM 只做「解释」，不做「事实」——输入是已由校验器复核的结构化摘要，
输出文本不进入任何下游决策（ADR-001）。文本按句切分为增量段供 SSE delta。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.adapter import LLMAdapter, LLMError, LLMRetryableError, UsageMeter

RESPONDER_SYSTEM = """你是 TripAgent 的行程助手。请用简短中文（不超过 120 字）向用户解释
刚刚生成的行程结果：覆盖了哪些天/景点、约束是否满足。只能基于给定摘要，
不得新增任何数字或景点名。"""

_CHUNK_RE = re.compile(r"[^。！？!?~]+[。！？!?~]?")

REPLY_MAX_CHARS = 400


def summarize_plan(plan_response_dict: dict[str, Any]) -> str:
    """PlanResponse → 紧凑事实摘要（Responder 的唯一事实来源）。"""
    if plan_response_dict.get("status") != "feasible":
        reasons = plan_response_dict.get("infeasible_reasons") or []
        return json.dumps(
            {"status": "infeasible", "reasons": reasons[:5]}, ensure_ascii=False
        )
    days = []
    for d in plan_response_dict.get("days", []):
        names = [s["name"] for s in d.get("stops", [])]
        label = f"第{d['date_index'] + 1}天:"
        days.append(label + "、".join(names) if names else label + "无行程")
    v = plan_response_dict.get("verification") or {}
    return json.dumps({
        "status": "feasible",
        "days": days,
        "recomputed_budget": v.get("recomputed_budget"),
        "violations": len(v.get("violations") or []),
    }, ensure_ascii=False)


def degraded_reply(plan_response_dict: dict[str, Any], constraints_echo: list[dict]) -> str:
    """规则模板降级文本（§7.1）。"""
    fields = "、".join(f"{c['field']}={c['value']}" for c in constraints_echo[:4]) or "当前约束"
    if plan_response_dict.get("status") == "feasible":
        n_days = len(plan_response_dict.get("days") or [])
        v = plan_response_dict.get("verification") or {}
        spend = v.get("recomputed_budget", 0)
        return (
            f"已按约束重新规划（{fields}）：{n_days} 天行程，独立校验器复核通过，"
            f"重算花费 {spend:.0f} 元未超预算。（规则模板回复：LLM 暂不可用）"
        )
    reasons = plan_response_dict.get("infeasible_reasons") or []
    top = reasons[0] if reasons else "约束无法同时满足"
    return (f"这轮没法同时满足你的约束（{top}）。可以试试调整天数或预算。"
            "（规则模板回复：LLM 暂不可用）")


def compose_reply(
    llm: LLMAdapter | None,
    *,
    user_message: str,
    plan_response_dict: dict[str, Any],
    constraints_echo: list[dict[str, Any]],
    meter: UsageMeter,
) -> str:
    if llm is None:
        meter.mark_degraded()
        return degraded_reply(plan_response_dict, constraints_echo)
    try:
        result = llm.chat(messages=[
            {"role": "system", "content": RESPONDER_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({
                    "user_message": user_message,
                    "summary": json.loads(summarize_plan(plan_response_dict)),
                    "constraints": constraints_echo,
                }, ensure_ascii=False),
            },
        ])
        meter.add(result)
        text = (result.text or "").strip()[:REPLY_MAX_CHARS]
        if text:
            return text
    except (LLMError, LLMRetryableError):
        pass
    meter.mark_degraded()
    return degraded_reply(plan_response_dict, constraints_echo)


def split_deltas(reply: str, max_chunks: int = 4) -> list[str]:
    """把回复切成少量增量段（SSE delta 事件用）。"""
    chunks = [c for c in _CHUNK_RE.findall(reply) if c.strip()]
    if not chunks:
        return [reply] if reply else []
    if len(chunks) <= max_chunks:
        return chunks
    # 超出段数时合并尾部
    head, tail = chunks[: max_chunks - 1], "".join(chunks[max_chunks - 1:])
    return head + [tail]
