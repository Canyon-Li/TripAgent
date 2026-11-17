"""写入 Policy（§7.2「带预算的边际价值判定」）：该不该写、写到哪个 scope、归档谁。

规则：
- 用户每轮原话先落 observation（scope=session:<sid>，source=user_stated），
  它是 Resolver 对话历史与 turn 计数的唯一来源——Ledger 即会话状态。
- 本轮 Resolver 显式产出的约束字段才写 assertion（不重复写未变化字段）。
- 字段→scope 固定映射：预算/偏好属用户画像（跨会话，B9 演示载体），
  天数/必去点/时长上限/酒店属当次行程（会话内多轮演进，B12）。
- 变化 = 新 assertion 的 supersedes_id 指向旧活跃条目（归档，不物理删除）。
- retract = 写 value=None 的 assertion 并 supersede 旧值（读取侧视为撤销）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.memory import ledger

# PlanRequest 字段 → 记忆作用域：user（跨会话）/ session（当次行程）
FIELD_SCOPES: dict[str, str] = {
    "budget": "user",
    "preferences": "user",
    "days": "session",
    "must_visit": "session",
    "daily_minutes_cap": "session",
    "hotel": "session",
}


def scope_for(field: str, *, user_id: str, session_id: str) -> str:
    kind = FIELD_SCOPES.get(field)
    if kind is None:
        raise ValueError(f"field not managed by memory policy: {field!r}")
    return f"user:{user_id}" if kind == "user" else f"session:{session_id}"


@dataclass
class WriteReport:
    """一次 apply 的写入判定结果（观测与测试断言用）。"""

    written: list[dict[str, Any]] = field(default_factory=list)    # 新写入（含 supersede 关系）
    superseded: list[int] = field(default_factory=list)           # 被归档的旧事件 id
    skipped_unchanged: list[str] = field(default_factory=list)    # 值未变化而跳过的字段


def next_turn(session_id: str) -> int:
    """下一轮轮次号 = 已有 observation 数 + 1（首轮 turn=1）。"""
    return ledger.observation_count(session_id) + 1


def record_observation(*, session_id: str, user_id: str, text: str, turn: int) -> int:
    return ledger.append_event(
        session_id=session_id,
        user_id=user_id,
        event_type="observation",
        content={"text": text, "turn": turn},
        source="user_stated",
        scope=f"session:{session_id}",
    )


def apply_assertions(
    *, session_id: str, user_id: str, fields: dict[str, Any], turn: int
) -> WriteReport:
    """把本轮显式约束写入 Ledger（边际价值判定：只写新出/变化的字段）。

    fields 的 value 为「合并后的最终值」（列表字段由 service 层先做并集），
    与旧活跃值相同则跳过；不同则 supersede 归档。
    """
    report = WriteReport()
    for name, value in fields.items():
        scope = scope_for(name, user_id=user_id, session_id=session_id)
        old = ledger.find_active_assertion(scope, name)
        if old is not None and old["content"].get("value") == value:
            report.skipped_unchanged.append(name)
            continue
        new_id = ledger.append_event(
            session_id=session_id,
            user_id=user_id,
            event_type="assertion",
            content={"field": name, "value": value, "turn": turn},
            source="user_stated",
            scope=scope,
            supersedes_id=old["id"] if old else None,
        )
        report.written.append({
            "field": name,
            "value": value,
            "event_id": new_id,
            "supersedes": old["id"] if old else None,
        })
        if old:
            report.superseded.append(old["id"])
    return report


def retract_field(*, session_id: str, user_id: str, field_name: str, turn: int) -> int:
    """撤销一个字段：写 value=None 并 supersede 旧活跃条目（读取侧视为已撤销）。"""
    scope = scope_for(field_name, user_id=user_id, session_id=session_id)
    old = ledger.find_active_assertion(scope, field_name)
    if old is None:
        raise ValueError(f"no active assertion to retract: {field_name!r} in {scope}")
    return ledger.append_event(
        session_id=session_id,
        user_id=user_id,
        event_type="assertion",
        content={"field": field_name, "value": None, "turn": turn, "retracted": True},
        source="user_stated",
        scope=scope,
        supersedes_id=old["id"],
    )
