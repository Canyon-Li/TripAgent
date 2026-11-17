"""API 契约 schema（§10 / TASKS 1.4）：PlanRequest / PlanResponse。

与 §10 示例对齐：请求含 city/days/hotel/budget/daily_minutes_cap/must_visit/
preferences/session_id；响应含 trace_id/status/days[].stops[]（arrive/leave HH:MM、
wait_min、门票）、applied_constraints、verification.violations、cost。
额外字段（solver 观测信息、date、name 等）为前端与 Trace 视图服务，不改变示例结构。
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SUPPORTED_CITY = "beijing"
HOTEL_ANCHORS = {"wangfujing": "hotel_wangfujing"}


class PlanRequest(BaseModel):
    city: str = SUPPORTED_CITY
    days: int = Field(default=3, ge=1, le=7)
    hotel: str = "wangfujing"
    budget: float = Field(gt=0)
    daily_minutes_cap: int = Field(default=480, ge=60, le=720)
    must_visit: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    session_id: str = "default"
    start_date: dt.date | None = Field(default=None)  # 缺省=请求当天

    @field_validator("city")
    @classmethod
    def _city_supported(cls, v: str) -> str:
        if v.lower() != SUPPORTED_CITY:
            raise ValueError(f"unsupported city: {v!r}（数据集仅覆盖 {SUPPORTED_CITY}，B5）")
        return v.lower()

    @field_validator("hotel")
    @classmethod
    def _hotel_known(cls, v: str) -> str:
        if v not in HOTEL_ANCHORS and v not in HOTEL_ANCHORS.values():
            raise ValueError(
                f"unknown hotel: {v!r}（可用锚点: {', '.join(sorted(HOTEL_ANCHORS))}）"
            )
        return v


class StopOut(BaseModel):
    poi: str
    name: str
    category: str
    arrive: str            # "HH:MM"
    leave: str             # "HH:MM"
    wait_min: int
    ticket: float
    note: str = ""


class DayOut(BaseModel):
    date_index: int
    date: dt.date
    depart: str            # 离开酒店 "HH:MM"
    return_: str = Field(alias="return")  # 回到酒店 "HH:MM"（return 为关键字，序列化别名）
    away_minutes: int
    stops: list[StopOut] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ViolationOut(BaseModel):
    type: str
    detail: str
    day_index: int | None = None
    poi: str | None = None


class VerificationOut(BaseModel):
    violations: list[ViolationOut] = Field(default_factory=list)
    recomputed_budget: float = 0.0


class AppliedConstraintsOut(BaseModel):
    """约束来源回显（§10）。source 形如 "request" / "memory_views#17"；turn 为首次提出轮次。"""

    budget: float
    source: str = "request"
    turn: int = 1
    # 逐字段 {field,value,source,turn}（阶段 2）
    constraints: list[dict] = Field(default_factory=list)


class CostOut(BaseModel):
    latency_ms: int = 0
    token_in: int = 0
    token_out: int = 0


class SolverOut(BaseModel):
    status: str = "unknown"
    objective: int = 0
    wall_ms: int = 0
    phases: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    trace_id: str
    status: Literal["feasible", "infeasible"]
    city: str
    days: list[DayOut] = Field(default_factory=list)  # infeasible 时不硬凑（TASKS 1.5）
    applied_constraints: AppliedConstraintsOut
    verification: VerificationOut = Field(default_factory=VerificationOut)
    infeasible_reasons: list[str] = Field(default_factory=list)
    cost: CostOut = CostOut()
    solver: SolverOut = SolverOut()


class ChatRequest(BaseModel):
    """POST /api/chat 请求（§10 SSE 流式对话；B9 无登录，session/user 直接声明）。"""

    session_id: str = Field(default="s-default", min_length=1, max_length=64)
    user_id: str = Field(default="u-demo", min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
