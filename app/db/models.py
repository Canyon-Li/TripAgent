"""数据表模型（§9 DDL）：runs / spans（阶段 0）+ memory_events / memory_views（阶段 2）
+ eval_results（阶段 3）+ capabilities（阶段 4 Registry）。"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Run(Base):
    __tablename__ = "runs"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 最终 PlanResponse（阶段 3：最近一次可行方案回退 + Trace 回放用）；失败/中断可为空
    response_json: Mapped[str | None] = mapped_column(Text)
    # running | succeeded | failed | interrupted
    status: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    git_sha: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO-8601
    finished_at: Mapped[str | None] = mapped_column(String)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_in: Mapped[int | None] = mapped_column(Integer)
    token_out: Mapped[int | None] = mapped_column(Integer)


class Span(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.trace_id"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    meta_json: Mapped[str | None] = mapped_column(Text)


class MemoryEvent(Base):
    """Ledger：追加写、不可变（ADR-003）。矛盾用 supersedes_id 归档，不物理删除。"""

    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    # assertion | belief | decision | observation（§9）
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    # user_stated | behavior | environment | agent_generated（Provenance）
    source: Mapped[str] = mapped_column(String, nullable=False)
    # session:<sid> | user:<uid> | global（§9 的 trip:<id> 按实际语义落为 session）
    scope: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    decay_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    supersedes_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("memory_events.id")
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO-8601


class MemoryView(Base):
    """派生视图（可从 Ledger 重放重建，§7.2）；valid_to 非空 = 已被更新版本替代。"""

    __tablename__ = "memory_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # hard_constraints | persona | summary（§9）
    view_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_key: Mapped[str] = mapped_column(String, nullable=False)  # = scope
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    derived_from: Mapped[str] = mapped_column(Text, nullable=False)  # event id list
    valid_from: Mapped[str] = mapped_column(String, nullable=False)
    valid_to: Mapped[str | None] = mapped_column(String)
    last_confirmed_at: Mapped[str | None] = mapped_column(String)


class EvalResult(Base):
    """评测结果（§9）：一次评测运行的每用例×指标一行，按 git_sha 聚合出趋势（看板）。"""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    git_sha: Mapped[str] = mapped_column(String, nullable=False)
    case_id: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO-8601


class Capability(Base):
    """能力卡（§7.4 Registry / §9）：一行 = 一个可路由能力版本。

    灰度权重与版本绑定存 SQLite、路由时读取——回滚只需更新一行记录（不重启，
    §7.4「回滚不重启」）。status：active（可路由）| unhealthy（心跳失败自动摘除，
    恢复自动回切）| retired（注销归档，不物理删除）。
    """

    __tablename__ = "capabilities"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    # tool | solver | verifier | llm_adapter（§9）
    kind: Mapped[str] = mapped_column(String, nullable=False)
    input_schema: Mapped[str] = mapped_column(Text, nullable=False)   # JSON 字符串
    output_schema: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 字符串
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    healthcheck: Mapped[str] = mapped_column(Text, nullable=False)    # 健康检查定义
    cost_estimate: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[str] = mapped_column(Text, nullable=False)           # 逗号分隔
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    traffic_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
