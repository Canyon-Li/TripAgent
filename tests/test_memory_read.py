"""2.4 Memory.read：scope 链裁决、衰减不丢硬约束、显式/记忆合并、用户隔离。"""

from __future__ import annotations

import datetime as dt

from app.memory import policy, service, views


def _seed(session_id: str, user_id: str, fields: dict, turn: int) -> None:
    """写入 + 刷新两个受影响 scope 的 view（与 chat 主链路同构）。"""
    policy.apply_assertions(session_id=session_id, user_id=user_id, fields=fields, turn=turn)
    for scope in {f"session:{session_id}", f"user:{user_id}"}:
        views.refresh_view(scope)


def test_scope_chain_session_overrides_user():
    _seed("sr-1", "u-a", {"budget": 9000}, turn=1)          # user scope
    _seed("sr-1", "u-a", {"budget": 5000}, turn=2)          # 仍 user scope（字段映射固定）
    # 手工造一个 session 级 budget 覆盖场景：直接向 session scope 写 assertion
    from app.memory import ledger

    ledger.append_event(
        session_id="sr-1", user_id="u-a", event_type="assertion",
        content={"field": "budget", "value": 3000, "turn": 3},
        source="user_stated", scope="session:sr-1",
    )
    views.refresh_view("session:sr-1")
    got = service.read_memory(session_id="sr-1", user_id="u-a")
    budget = next(c for c in got if c.field == "budget")
    assert budget.value == 3000 and budget.scope == "session:sr-1"


def test_user_scope_cross_session_injection():
    """跨会话记忆（B9）：user 级约束在新 session 仍注入。"""
    _seed("sr-old", "u-alice", {"budget": 5000}, turn=1)
    got = service.read_memory(session_id="sr-new", user_id="u-alice")
    assert [(c.field, c.value) for c in got] == [("budget", 5000)]
    assert got[0].source.startswith("memory_views#") and got[0].turn == 1


def test_user_isolation():
    _seed("sr-2", "u-alice", {"budget": 5000}, turn=1)
    assert service.read_memory(session_id="sr-2b", user_id="u-bob") == []


def test_global_scope_weakest():
    from app.memory import ledger

    ledger.append_event(
        session_id="g", user_id="sys", event_type="assertion",
        content={"field": "hotel", "value": "wangfujing", "turn": 0},
        source="environment", scope="global",
    )
    views.refresh_view("global")
    got = service.read_memory(session_id="sr-3", user_id="u-x")
    assert [(c.field, c.value) for c in got] == [("hotel", "wangfujing")]
    # session 覆盖 global
    _seed("sr-3", "u-x", {"hotel": "wangfujing2"}, turn=1)
    got2 = service.read_memory(session_id="sr-3", user_id="u-x")
    assert next(c for c in got2 if c.field == "hotel").scope == "session:sr-3"


def test_decay_lowers_confidence_but_never_drops_hard_constraint():
    _seed("sr-4", "u-a", {"budget": 7000}, turn=1)
    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=120)  # 4 个半衰期
    got = service.read_memory(session_id="sr-4", user_id="u-a", now=far_future)
    budget = next(c for c in got if c.field == "budget")
    assert budget is not None  # 衰减 4 个半衰期仍注入（硬约束不丢弃）
    assert budget.confidence < 0.07  # 0.5^4 ≈ 0.0625
    fresh = service.read_memory(session_id="sr-4", user_id="u-a")
    assert next(c for c in fresh if c.field == "budget").confidence == 1.0


def test_merge_explicit_overrides_scalar_unions_lists():
    _seed("sr-5", "u-a", {"budget": 10000, "must_visit": ["forbidden_city"], "days": 3}, turn=1)
    memory = service.read_memory(session_id="sr-5", user_id="u-a")
    merged = service.merge_constraints(
        {"budget": 8000, "must_visit": ["summer_palace"]}, memory, turn=2,
    )
    by = {c.field: c for c in merged}
    assert by["budget"].value == 8000 and by["budget"].source == "request"
    assert by["must_visit"].value == ["summer_palace", "forbidden_city"]
    # 列表并集溯源保留记忆侧：首提轮次/来源不被本轮覆写（TASKS 2.10 口径）
    assert by["must_visit"].source.startswith("memory_views#")
    assert by["must_visit"].turn == 1
    assert by["days"].value == 3 and by["days"].source.startswith("memory_views#")
    assert by["days"].turn == 1


def test_merge_memory_fills_missing_field():
    """模型漏抽的字段由记忆兜底（多轮滑点防护的核心断言）。"""
    _seed("sr-6", "u-a", {"budget": 10000}, turn=1)
    memory = service.read_memory(session_id="sr-6", user_id="u-a")
    merged = service.merge_constraints({"days": 2}, memory, turn=6)
    by = {c.field: c for c in merged}
    assert by["budget"].value == 10000 and by["budget"].turn == 1


def test_merge_none_explicit_skipped():
    _seed("sr-7", "u-a", {"budget": 1000}, turn=1)
    memory = service.read_memory(session_id="sr-7", user_id="u-a")
    merged = service.merge_constraints({"budget": None}, memory, turn=2)
    assert {c.field: c.value for c in merged}["budget"] == 1000
