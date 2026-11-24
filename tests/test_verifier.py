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


def test_cost_breakdown_reconciles() -> None:
    """费用构成拆分（阶段 5 前置修复）：逐日门票/车费各自加总，与总花费对平。

    dogfood 发现（data/badcases.jsonl 首条）：此前响应只下发总额，用户把行程卡
    上可见的站点门票加总对不上总额——差额（车费）不可见，怀疑系统乱算。
    计算本就正确（独立重算 + 属性测试 oracle），此处只补可见性：拆分数与总额
    必须严格咬合，用户才能自己把账对平。
    """
    repo = get_repository()
    req = SolveRequest(
        days=3, budget=10_000, daily_minutes_cap=480,
        must_visit=frozenset({"forbidden_city"}), preferences=frozenset(), start_date=WED,
    )
    r = solve_plan(repo, req)
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
    assert out.violations == []
    assert out.tickets_cny > 0 and out.fare_cny > 0
    # 逐日小计加总 = 总拆分；门票 + 车费 = 总花费（对平判据）
    assert round(sum(a.ticket_cny for a in out.day_audits), 2) == out.tickets_cny
    assert round(sum(a.fare_cny for a in out.day_audits), 2) == out.fare_cny
    assert round(out.tickets_cny + out.fare_cny, 2) == out.recomputed_budget
    # 门票拆分与 POI 目录独立核对（车费 = 总花费 − 门票）
    visited = {s.poi_id for d in r.days for s in d.stops}
    catalog_tickets = round(sum(repo.poi(p).ticket for p in visited), 2)
    assert out.tickets_cny == catalog_tickets


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
