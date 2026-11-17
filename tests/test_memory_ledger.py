"""2.2 Ledger 追加写 + Policy：不可变账本、supersedes 归档、边际价值写入、turn 计数。"""

from __future__ import annotations

import pytest

from app.memory import ledger, policy


def test_append_and_list_roundtrip():
    eid = ledger.append_event(
        session_id="s-1", user_id="u-a", event_type="observation",
        content={"text": "你好", "turn": 1}, source="user_stated", scope="session:s-1",
    )
    events = ledger.list_events(session_id="s-1")
    assert len(events) == 1
    e = events[0]
    assert e["id"] == eid
    assert e["content"] == {"text": "你好", "turn": 1}
    assert e["event_type"] == "observation"
    assert e["supersedes_id"] is None
    assert e["created_at"]  # UTC ISO 非空


def test_append_rejects_unknown_enums():
    with pytest.raises(ValueError, match="event_type"):
        ledger.append_event(
            session_id="s", user_id="u", event_type="hypothesis",
            content={}, source="user_stated", scope="global",
        )
    with pytest.raises(ValueError, match="source"):
        ledger.append_event(
            session_id="s", user_id="u", event_type="assertion",
            content={}, source="guess", scope="global",
        )


def test_policy_field_scopes():
    assert policy.scope_for("budget", user_id="u-a", session_id="s-1") == "user:u-a"
    assert policy.scope_for("must_visit", user_id="u-a", session_id="s-1") == "session:s-1"
    with pytest.raises(ValueError, match="not managed"):
        policy.scope_for("city", user_id="u-a", session_id="s-1")


def test_apply_assertions_new_fields_written():
    report = policy.apply_assertions(
        session_id="s-2", user_id="u-a",
        fields={"budget": 5000, "must_visit": ["forbidden_city"]}, turn=1,
    )
    assert [w["field"] for w in report.written] == ["budget", "must_visit"]
    assert report.superseded == []
    # budget 落 user scope，must_visit 落 session scope
    assert ledger.find_active_assertion("user:u-a", "budget")["content"]["value"] == 5000
    mv = ledger.find_active_assertion("session:s-2", "must_visit")
    assert mv["content"]["value"] == ["forbidden_city"]


def test_apply_assertions_unchanged_skipped_changed_supersedes():
    policy.apply_assertions(
        session_id="s-3", user_id="u-b", fields={"budget": 10000}, turn=1,
    )
    # 相同值：跳过
    r2 = policy.apply_assertions(
        session_id="s-3", user_id="u-b", fields={"budget": 10000}, turn=2,
    )
    assert r2.skipped_unchanged == ["budget"] and not r2.written
    # 变化值：归档旧条目
    r3 = policy.apply_assertions(
        session_id="s-3", user_id="u-b", fields={"budget": 8000}, turn=3,
    )
    assert len(r3.written) == 1 and r3.superseded == [r3.written[0]["supersedes"]]
    # 活跃视图只剩新值；Ledger 两条都在（可回放）
    assert ledger.find_active_assertion("user:u-b", "budget")["content"]["value"] == 8000
    all_budget = [
        e for e in ledger.list_events(scope="user:u-b", event_type="assertion")
        if e["content"]["field"] == "budget"
    ]
    assert len(all_budget) == 2
    assert all_budget[1]["supersedes_id"] == all_budget[0]["id"]


def test_observation_turn_counting():
    assert policy.next_turn("s-turn") == 1
    policy.record_observation(session_id="s-turn", user_id="u-a", text="第一轮", turn=1)
    assert policy.next_turn("s-turn") == 2
    policy.record_observation(session_id="s-turn", user_id="u-a", text="第二轮", turn=2)
    assert ledger.observation_count("s-turn") == 2


def test_retract_field():
    policy.apply_assertions(session_id="s-4", user_id="u-c", fields={"budget": 3000}, turn=1)
    policy.retract_field(session_id="s-4", user_id="u-c", field_name="budget", turn=2)
    active = ledger.find_active_assertion("user:u-c", "budget")
    assert active["content"]["value"] is None and active["content"]["retracted"] is True
    with pytest.raises(ValueError, match="no active assertion"):
        policy.retract_field(session_id="s-4", user_id="u-c", field_name="days", turn=3)


def test_scope_isolation_between_users_and_sessions():
    policy.apply_assertions(
        session_id="s-5", user_id="u-alice", fields={"budget": 5000, "days": 3}, turn=1,
    )
    # 用户隔离：u-bob 的 user scope 无 alice 的预算
    assert ledger.find_active_assertion("user:u-bob", "budget") is None
    # 会话隔离：另一 session 无 s-5 的 days
    assert ledger.find_active_assertion("session:s-6", "days") is None
