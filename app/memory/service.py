"""Memory.read 读取链路（§7.2）：scope 链过滤 → 时间衰减加权 → 置信度排序 → 结构化输出。

固定链路的 Memory.read 是权威注入通道（B15）；输出为结构化硬约束对象
（不是自然语言摘要），直接供 /api/chat 与 Resolver 约束重述使用。

设计要点：硬约束不因时间衰减被丢弃（未被 supersede 即注入）——衰减只影响
注入顺序与置信度展示。否则「第 6 轮仍召回首轮预算」的验收口径会被衰减破坏。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any

from app.memory import ledger, policy, views

HALF_LIFE_DAYS_DEFAULT = 30.0  # §7.2 时间衰减：30 天半衰期


@dataclass(frozen=True)
class ConstraintOrigin:
    """一条注入约束 + 完整溯源（§10 applied_constraints 的数据源）。"""

    field: str
    value: Any
    source: str          # request | memory_views#<view_id>
    scope: str           # request | session:<sid> | user:<uid> | global
    turn: int            # 约束首次提出的轮次（request = 本轮）
    event_id: int | None = None
    view_id: int | None = None
    confidence: float = 1.0  # 衰减后的有效置信度

    def to_public(self) -> dict[str, Any]:
        """对外回显（schema AppliedConstraintsOut.constraints[] 的元素）。"""
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "turn": self.turn,
        }


def read_memory(
    *,
    session_id: str,
    user_id: str,
    now: dt.datetime | None = None,
    half_life_days: float = HALF_LIFE_DAYS_DEFAULT,
) -> list[ConstraintOrigin]:
    """按 scope 链 session → user → global 读取活跃硬约束（前者优先），按有效置信度降序。"""
    now = now or dt.datetime.now(dt.UTC)
    chain = policy.scope_chain(session_id, user_id)
    acc: dict[str, ConstraintOrigin] = {}
    for scope in chain:
        v = views.current_view(scope)
        if v is None:
            continue
        constraints = v["value"]["constraints"]
        for field, prov in v["value"]["provenance"].items():
            if field in acc:  # 靠前 scope 已覆盖（冲突裁决：session > user > global）
                continue
            event = ledger.get_event(prov["event_id"])
            age_days = 0.0
            if event:
                created = dt.datetime.fromisoformat(event["created_at"])
                age_days = max(0.0, (now - created).total_seconds() / 86400)
            effective = prov["confidence"] * 0.5 ** (age_days / half_life_days)
            acc[field] = ConstraintOrigin(
                field=field,
                value=constraints[field],
                source=f"memory_views#{v['id']}",
                scope=scope,
                turn=prov["turn"] or 0,
                event_id=prov["event_id"],
                view_id=v["id"],
                confidence=round(effective, 4),
            )
    return sorted(acc.values(), key=lambda c: -c.confidence)


def merge_constraints(
    explicit: dict[str, Any],
    memory: list[ConstraintOrigin],
    *,
    turn: int,
) -> list[ConstraintOrigin]:
    """本轮显式约束（Resolver 产出）与记忆注入合并（B12：与既有约束合并后全量重规划）。

    - 单值字段：显式优先（记忆兜底——即使模型漏抽字段，约束链路也不滑点）；
    - 列表字段（must_visit / preferences）：并集，显式在前、保序去重；
      溯源保留记忆侧（首提轮次/来源不因本轮并集被覆写）；
    - value=None（retract）的显式项跳过，不进入约束集。
    """
    out: dict[str, ConstraintOrigin] = {c.field: c for c in memory}
    for field, value in explicit.items():
        if value is None:
            continue
        existing = out.get(field)
        if existing is not None and isinstance(existing.value, list) and isinstance(value, list):
            merged = list(dict.fromkeys(value + existing.value))
            # 溯源保留记忆侧（若存在）：并集字段的首提轮次/来源以最早成员进入
            # 约束集时为准（TASKS 2.10「首提轮次」口径），不被本轮并集覆写抹掉
            out[field] = replace(existing, value=merged)
        else:
            out[field] = ConstraintOrigin(
                field=field, value=value, source="request", scope="request", turn=turn,
            )
    return list(out.values())


def origins_to_fields(origins: list[ConstraintOrigin]) -> dict[str, Any]:
    """约束列表 → {field: value} 平铺（构造 PlanRequest / 写回 Ledger 用）。"""
    return {c.field: c.value for c in origins}
