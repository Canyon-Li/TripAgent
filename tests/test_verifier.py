"""独立校验器测试（TASKS 1.3 / ADR-001）：故意构造各类违反，逐类命中。"""

from __future__ import annotations

import datetime as dt

from app.db.repository import get_repository
from app.solver.routing import SolveRequest, solve_plan
from app.verifier.verify import (
    DayClaim,
    StopClaim,
    VerifyRequest,
    verify_itinerary,
)

WED = dt.date(2026, 11, 18)
MON = dt.date(2026, 11, 23)


def _vreq(**kw) -> VerifyRequest:
    base = dict(
        days=3, budget=10_000, daily_minutes_cap=480,
        must_visit=frozenset(), start_date=WED,
    )
    base.update(kw)
    return VerifyRequest(**base)


def _travel(a: str, b: str) -> int:
    repo = get_repository()
    return repo.matrix.minutes[repo.matrix.index_of(a)][repo.matrix.index_of(b)]


def test_solver_output_passes_verification() -> None:
    """端到端最小闭环：真实求解结果 → 独立校验零冲突。"""
    repo = get_repository()
    req = SolveRequest(
        days=3, budget=10_000, daily_minutes_cap=480,
        must_visit=frozenset({"forbidden_city"}), preferences=frozenset(), start_date=WED,
    )
    r = solve_plan(repo, req)
    assert r.status == "solved"
    claims = [
        DayClaim(
            date_index=d.date_index,
            depart_min=d.depart_min,
            stops=tuple(
                StopClaim(s.poi_id, s.arrive_min, s.leave_min) for s in d.stops
            ),
        )
        for d in r.days
    ]
    out = verify_itinerary(repo, _vreq(must_visit=frozenset({"forbidden_city"})), claims)
    assert out.violations == [], f"unexpected violations: {out.violations}"
    assert out.recomputed_budget > 0


def test_window_closed_detected() -> None:
    """故宫 15:30 进入、18:30 才离开，超过 17:00 闭馆。"""
    repo = get_repository()
    t = _travel("hotel_wangfujing", "forbidden_city")
    depart = 15 * 60 + 30 - t
    arrive, leave = 15 * 60 + 30, 15 * 60 + 30 + 180
    claims = [DayClaim(0, depart, (StopClaim("forbidden_city", arrive, leave),))]
    out = verify_itinerary(repo, _vreq(days=1), claims)
    assert "window_closed" in out.types()


def test_timeline_mismatch_detected() -> None:
    """声明的到达时刻与按矩阵重算不一致。"""
    repo = get_repository()
    t = _travel("hotel_wangfujing", "tiananmen_square")
    depart = 9 * 60
    raw = depart + t
    claims = [
        DayClaim(0, depart, (StopClaim("tiananmen_square", raw + 7, raw + 7 + 60),))
    ]
    out = verify_itinerary(repo, _vreq(days=1), claims)
    assert "timeline_mismatch" in out.types()


def test_duplicate_visit_detected() -> None:
    """同一 POI 两天各出现一次。"""
    repo = get_repository()
    t = _travel("hotel_wangfujing", "tiananmen_square")
    depart = 9 * 60
    arrive = depart + t
    stop = StopClaim("tiananmen_square", arrive, arrive + 60)
    claims = [DayClaim(0, depart, (stop,)), DayClaim(1, depart, (stop,))]
    out = verify_itinerary(repo, _vreq(days=2), claims)
    assert "duplicate_visit" in out.types()


def test_closed_weekday_detected() -> None:
    """故宫周一闭馆，周一到访。"""
    repo = get_repository()
    t = _travel("hotel_wangfujing", "forbidden_city")
    depart = 8 * 60 + 30 - t if 8 * 60 + 30 > t else 0
    arrive = max(depart + t, 8 * 60 + 30)
    claims = [DayClaim(0, depart, (StopClaim("forbidden_city", arrive, arrive + 180),))]
    out = verify_itinerary(repo, _vreq(days=1, start_date=MON), claims)
    assert "closed_weekday" in out.types()


def test_daily_cap_exceeded_detected() -> None:
    repo = get_repository()
    t = _travel("hotel_wangfujing", "tiananmen_square")
    depart = 9 * 60
    arrive = depart + t
    claims = [DayClaim(0, depart, (StopClaim("tiananmen_square", arrive, arrive + 60),))]
    out = verify_itinerary(repo, _vreq(days=1, daily_minutes_cap=10), claims)
    assert "daily_cap_exceeded" in out.types()


def test_budget_exceeded_detected() -> None:
    repo = get_repository()
    t = _travel("hotel_wangfujing", "wangfujing_street")
    depart = 10 * 60
    arrive = depart + t
    claims = [DayClaim(0, depart, (StopClaim("wangfujing_street", arrive, arrive + 90),))]
    out = verify_itinerary(repo, _vreq(days=1, budget=1), claims)
    assert "budget_exceeded" in out.types()
    assert out.recomputed_budget > 1


def test_must_visit_missed_and_unknown_poi_and_bad_day() -> None:
    repo = get_repository()
    claims = [
        DayClaim(0, 9 * 60, (StopClaim("not_a_poi", 600, 660),)),   # unknown
        DayClaim(5, 9 * 60, ()),                                     # bad index
    ]
    out = verify_itinerary(repo, _vreq(days=2, must_visit=frozenset({"beihai_park"})), claims)
    assert out.types() == {"unknown_poi", "invalid_day_index", "must_visit_missed"}


def test_empty_days_are_valid() -> None:
    repo = get_repository()
    out = verify_itinerary(repo, _vreq(days=3), [DayClaim(0, 480, ()), DayClaim(1, 480, ())])
    assert out.ok
    assert out.recomputed_budget == 0.0
