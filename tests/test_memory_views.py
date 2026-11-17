"""2.3 Views：派生、版本替代（valid_to）、retract 生效、Ledger 重放重建一致性。"""

from __future__ import annotations

from app.memory import ledger, policy, views


def test_refresh_creates_view_with_provenance():
    policy.apply_assertions(
        session_id="sv-1", user_id="u-a",
        fields={"budget": 5000, "must_visit": ["forbidden_city"]}, turn=1,
    )
    v_user = views.refresh_view("user:u-a")
    v_sess = views.refresh_view("session:sv-1")
    assert v_user is not None and v_sess is not None
    assert v_user["value"]["constraints"] == {"budget": 5000}
    assert v_user["value"]["provenance"]["budget"]["turn"] == 1
    assert v_sess["value"]["constraints"] == {"must_visit": ["forbidden_city"]}
    assert len(v_user["derived_from"]) == 1 and len(v_sess["derived_from"]) == 1


def test_refresh_supersedes_old_view_version():
    policy.apply_assertions(session_id="sv-2", user_id="u-b", fields={"budget": 10000}, turn=1)
    views.refresh_view("user:u-b")
    policy.apply_assertions(session_id="sv-2", user_id="u-b", fields={"budget": 8000}, turn=3)
    v2 = views.refresh_view("user:u-b")
    assert v2["value"]["constraints"]["budget"] == 8000
    assert views.current_view("user:u-b")["id"] == v2["id"]

    # 旧版本 valid_to 已关闭（历史版本可查、可回放）
    from sqlalchemy import select  # noqa: E402

    from app.db.base import SessionLocal  # noqa: E402
    from app.db.models import MemoryView  # noqa: E402

    with SessionLocal() as db:
        all_versions = db.scalars(
            select(MemoryView).where(MemoryView.subject_key == "user:u-b")
        ).all()
    assert len(all_versions) == 2
    closed = [v for v in all_versions if v.id != v2["id"]]
    assert closed and closed[0].valid_to is not None


def test_retract_removed_from_view():
    policy.apply_assertions(session_id="sv-3", user_id="u-c", fields={"budget": 3000}, turn=1)
    policy.retract_field(session_id="sv-3", user_id="u-c", field_name="budget", turn=2)
    v = views.refresh_view("user:u-c")
    assert v is not None  # 有活跃（已撤销）assertion 仍派生 view
    assert v["value"]["constraints"] == {}  # 撤销字段不出现


def test_empty_scope_no_view():
    assert views.refresh_view("session:never-written") is None
    assert views.current_view("session:never-written") is None


def test_rebuild_consistent_with_incremental():
    # 增量路径：多轮写入 + 每次刷新
    policy.apply_assertions(
        session_id="sv-4", user_id="u-d",
        fields={"budget": 10000, "days": 3, "must_visit": ["forbidden_city"]}, turn=1,
    )
    views.refresh_view("user:u-d")
    views.refresh_view("session:sv-4")
    policy.apply_assertions(session_id="sv-4", user_id="u-d", fields={"budget": 8000}, turn=3)
    views.refresh_view("user:u-d")
    policy.apply_assertions(
        session_id="sv-4", user_id="u-d",
        fields={"must_visit": ["forbidden_city", "summer_palace"]}, turn=4,
    )
    views.refresh_view("session:sv-4")

    result = views.rebuild_views()
    assert result["consistent"] is True
    # 重建后 view 值与增量一致（budget 新值、must_visit 并集后终值）
    v = views.current_view("user:u-d")
    assert v["value"]["constraints"] == {"budget": 8000}
    assert views.current_view("session:sv-4")["value"]["constraints"] == {
        "days": 3, "must_visit": ["forbidden_city", "summer_palace"]
    }


def test_rebuild_detects_tampering():
    """view 被篡改时 rebuild 应报不一致（Ledger 是唯一权威事实来源）。"""
    policy.apply_assertions(session_id="sv-5", user_id="u-e", fields={"budget": 2000}, turn=1)
    views.refresh_view("user:u-e")
    # 直接改库模拟视图损坏（正常运行不会发生——这正是 rebuild 要防的）
    from sqlalchemy import update  # noqa: E402

    from app.db.base import SessionLocal  # noqa: E402
    from app.db.models import MemoryView  # noqa: E402

    with SessionLocal() as db:
        db.execute(
            update(MemoryView)
            .where(MemoryView.subject_key == "user:u-e", MemoryView.valid_to.is_(None))
            .values(value_json='{"constraints": {"budget": 1}, "provenance": {}}')
        )
        db.commit()
    result = views.rebuild_views()
    assert result["consistent"] is False
    # 重建修复回权威值
    assert views.current_view("user:u-e")["value"]["constraints"] == {"budget": 2000}


def test_ledger_events_survive_rebuild():
    """重建只动 views，Ledger 事件一条不少（不可变账本）。"""
    policy.apply_assertions(session_id="sv-6", user_id="u-f", fields={"budget": 900}, turn=1)
    before = ledger.list_events()
    views.rebuild_views()
    assert ledger.list_events() == before
