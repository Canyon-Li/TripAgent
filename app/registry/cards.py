"""能力卡 schema（§7.4 / §9 capabilities 表的 API 侧形态）。

DB 行的 tags 是逗号分隔字符串、input/output_schema 是 JSON 字符串，
本模块负责行 ↔ 卡互转；卡本身用 pydantic 强校验（POST 注册入口）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["tool", "solver", "verifier", "llm_adapter"]
# active=可路由 | unhealthy=心跳失败自动摘除（恢复回切）| retired=注销归档
Status = Literal["active", "unhealthy", "retired"]


class CapabilityCard(BaseModel):
    """一行 = 一个可路由能力版本（PK: name+version）。"""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    kind: Kind
    input_schema: dict
    output_schema: dict
    endpoint: str
    healthcheck: dict
    cost_estimate: float | None = None
    tags: list[str] = Field(default_factory=list)
    status: Status = "active"
    traffic_weight: int = Field(default=100, ge=0, le=10_000)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"
