"""评测用例 schema（§7.3）：plan 单轮 + chat 多轮两种，JSONL 一行一条。

文件首行为 `_meta`（评测集版本/总数，§12 报告须注明评测集版本）。
chat 用例的 turns 内嵌 fake_llm 脚本：CI 无 key 时按脚本驱动（确定性回归），
真实评测（--real）忽略脚本走真 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator


class PlanRequestSpec(BaseModel):
    """与 /api/plan 请求体同构（schemas.PlanRequest 的宽松版：session_id 由用例层注入）。"""

    city: str = "beijing"
    days: int = Field(gt=0, le=10)
    hotel: str = "wangfujing"
    budget: float = Field(ge=0)
    daily_minutes_cap: int = Field(default=480, gt=0, le=1440)
    must_visit: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    session_id: str = "seed"
    start_date: str  # ISO 日期（评测确定性：不依赖运行当天星期）

    @field_validator("start_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        import datetime as dt

        dt.date.fromisoformat(v)
        return v


class PlanAsserts(BaseModel):
    status: Literal["feasible", "infeasible"]
    must_visit_covered: bool = True
    budget_within: bool = True
    time_window_violations: int = 0
    days_count: int | None = None
    reason_contains: list[str] = Field(default_factory=list)


class PlanCase(BaseModel):
    case_id: str
    hidden: bool = False
    kind: Literal["plan"] = "plan"
    request: PlanRequestSpec
    asserts: PlanAsserts


class FakeStep(BaseModel):
    """fake_llm 脚本一步：tool_calls（Resolver 轮）/ text（Responder 轮）/ error（触发降级）。"""

    tool_calls: list[tuple[str, str]] = Field(default_factory=list)
    text: str = ""
    error: Literal["retryable", "fatal"] | None = None


class ChatTurn(BaseModel):
    message: str
    fake_llm: list[FakeStep] = Field(default_factory=list)
    session_id: str | None = None  # 覆盖用例级 session（跨会话用例：user scope 记忆注入）


class MemoryRecallAssert(BaseModel):
    """memory_recall@N（§12）：末轮 applied_constraints 里该字段的回显。"""

    field: str
    value: float | int | str | list[str]
    source_prefix: str | None = None  # 如 "memory_views#"（记忆注入）
    turn: int | None = None  # 当前值确立轮次


class ChatAsserts(BaseModel):
    final_status: Literal["feasible", "infeasible", "clarification"]
    turn_types: list[Literal["plan", "clarification"]] | None = None  # 每轮 done.type
    memory_recall: list[MemoryRecallAssert] = Field(default_factory=list)
    must_visit_covered: bool = True  # 仅 final feasible 时适用
    budget_within: bool = True
    time_window_violations: int = 0
    reason_contains: list[str] = Field(default_factory=list)


class ChatCase(BaseModel):
    case_id: str
    hidden: bool = False
    kind: Literal["chat"] = "chat"
    user_id: str = "u-eval"
    session_id: str
    # 固定起始日期（评测确定性：周一闭馆类断言不随运行日漂移，§15）
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    turns: list[ChatTurn] = Field(min_length=1)
    asserts: ChatAsserts

    @field_validator("start_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        import datetime as dt

        dt.date.fromisoformat(v)
        return v


EvalCase = Annotated[PlanCase | ChatCase, Field(discriminator="kind")]

_CASE_ADAPTER: TypeAdapter[EvalCase] = TypeAdapter(EvalCase)


class EvalSetMeta(BaseModel):
    version: str
    count: int
    public: int
    hidden: int


def load_eval_cases(path: str | Path) -> tuple[EvalSetMeta, list[EvalCase]]:
    """读 JSONL：首行 _meta + N 条用例；schema 校验 + 计数核对（count 与实际一致）。"""
    lines = [
        line for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines or "_meta" not in json.loads(lines[0]):
        raise ValueError(f"评测集缺 _meta 首行: {path}")
    meta = EvalSetMeta.model_validate(json.loads(lines[0])["_meta"])
    raw = [json.loads(line) for line in lines[1:]]
    for item in raw:
        item.setdefault("kind", "plan")  # 阶段 1 遗留 plan 用例无 kind 字段
    cases = [_CASE_ADAPTER.validate_python(item) for item in raw]
    hidden = sum(1 for c in cases if c.hidden)
    if len(cases) != meta.count or hidden != meta.hidden or len(cases) - hidden != meta.public:
        raise ValueError(
            f"_meta 计数不符: 声明 {meta.count}(公开 {meta.public}/隐藏 {meta.hidden})，"
            f"实际 {len(cases)}(隐藏 {hidden})"
        )
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        dupes = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"case_id 重复: {sorted(dupes)}")
    return meta, cases
