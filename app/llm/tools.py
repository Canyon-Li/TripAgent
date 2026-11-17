"""白名单 function calling 工具（B14 / ADR-008 / §7.6）：仅 3 个，schema 与护栏强校验。

LLM 可见的全部工具就是这份 WHITELIST——求解器 / 校验器 / 记忆写入等能力
**不暴露给模型**（由运行时按固定链路强制调用，§7.6 表格的分界）。
参数 JSON Schema 与下方 pydantic 模型必须保持一致（tests 契约断言守住）。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

WHITELIST: frozenset[str] = frozenset(
    {"submit_plan_request", "ask_clarification", "recall_constraint"}
)
MAX_TOOL_CALLS = 3  # 单次请求调用上限（B14 防循环）


class SubmitPlanRequestArgs(BaseModel):
    """只上报用户本轮明确说出的字段（缺失 = None，不编造，B12 逐轮演进）。"""

    city: str = "beijing"
    days: int | None = Field(default=None, ge=1, le=7)
    budget: float | None = Field(default=None, gt=0)
    daily_minutes_cap: int | None = Field(default=None, ge=60, le=720)
    must_visit: list[str] | None = None
    preferences: list[str] | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class AskClarificationArgs(BaseModel):
    field: str = Field(min_length=1)  # budget / days / must_visit / ...
    reason: str = Field(min_length=1)


class RecallConstraintArgs(BaseModel):
    key: str = Field(min_length=1)  # 任意约束字段名


ARGS_MODELS: dict[str, type[BaseModel]] = {
    "submit_plan_request": SubmitPlanRequestArgs,
    "ask_clarification": AskClarificationArgs,
    "recall_constraint": RecallConstraintArgs,
}


def _schema(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


SUBMIT_PROPS: dict[str, Any] = {
    "city": {"type": "string", "enum": ["beijing"], "description": "城市（本项目仅北京）"},
    "days": {"type": "integer", "minimum": 1, "maximum": 7, "description": "行程天数"},
    "budget": {"type": "number", "exclusiveMinimum": 0, "description": "总预算（元）"},
    "daily_minutes_cap": {
        "type": "integer", "minimum": 60, "maximum": 720,
        "description": "每日在外时长上限（分钟）",
    },
    "must_visit": {"type": "array", "items": {"type": "string"}, "description": "必去 POI id"},
    "preferences": {"type": "array", "items": {"type": "string"}, "description": "偏好类目"},
    "confidence": {"type": "number", "description": "抽取置信度 0-1"},
}

TOOLS_SCHEMA: list[dict[str, Any]] = [
    _schema(
        "submit_plan_request",
        "提交结构化行程约束。只包含用户本轮消息中明确说出的字段；"
        "没提到的字段一律省略（不要编造预算或天数）。",
        SUBMIT_PROPS,
        [],
    ),
    _schema(
        "ask_clarification",
        "缺少关键字段且既有约束也无法补齐时，向用户提出澄清问题。",
        {
            "field": {"type": "string", "description": "缺失的字段名（budget/days/...）"},
            "reason": {"type": "string", "description": "为什么要问 / 建议怎么说"},
        },
        ["field", "reason"],
    ),
    _schema(
        "recall_constraint",
        "用户显式引用历史约束时（如“还是按上次那个预算”）检索该约束的当前值。",
        {"key": {"type": "string", "description": "约束字段名（budget/days/must_visit/...）"}},
        ["key"],
    ),
]

# 规则模板降级解析的偏好关键词（中文口语 → 类目 id，与前端 CATS 一致）
PREFERENCE_KEYWORDS: dict[str, list[str]] = {
    "history": ["历史", "文物", "古迹", "故宫"],
    "food": ["美食", "小吃", "吃的", "餐馆"],
    "nature": ["自然", "公园", "山水", "风景"],
    "modern": ["现代", "科技", "艺术"],
    "shopping": ["购物", "逛街", "商场"],
    "museum": ["博物馆", "博物馆"],
    "religion": ["寺庙", "宗教", "雍和宫"],
    "landmark": ["地标", "打卡", "网红"],
}


def validate_tool_call(name: str, arguments_json: str) -> tuple[BaseModel | None, str | None]:
    """护栏单步校验：白名单 → JSON 解析 → pydantic 强校验。

    返回 (args_model, violation)。violation 非 None 时 args 为 None：
    - non_whitelist_tool:name —— 不在白名单，直接拒绝（B14）
    - invalid_arguments_json —— 参数不是合法 JSON
    - schema_validation_failed —— 参数不符合 schema
    """
    if name not in WHITELIST:
        return None, f"non_whitelist_tool:{name}"
    try:
        raw = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError as exc:
        return None, f"invalid_arguments_json:{exc.msg}"
    model = ARGS_MODELS[name]
    try:
        return model.model_validate(raw), None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        return None, f"schema_validation_failed:{name}.{loc}:{first['msg']}"


def tools_payload_for_request() -> list[dict[str, Any]]:
    """发给 OpenAI-compatible 端点的 tools 参数（B17 原生透传）。"""
    return TOOLS_SCHEMA


ToolKind = Literal["submit", "clarify", "recall"]
