"""Solver v1 测试（TASKS 1.2）：真实数据集上的建模正确性与不可行处理。"""

from __future__ import annotations

import datetime as dt

from app.db.repository import get_repository
from app.solver.routing import SolveRequest, SolverParams, solve_plan

WED = dt.date(2026, 11, 18)   # 周三
MON = dt.date(2026, 11, 23)   # 周一（forbidden_city / national_museum / capital_museum 闭馆）


def _req(**kw) -> SolveRequest:
    base = dict(
        days=3,
        budget=10_000,
        daily_minutes_cap=480,
        must_visit=frozenset(),
        preferences=frozenset(),
        start_date=WED,
    )
    base.update(kw)
    return SolveRequest(**base)


def test_basic_3day_feasible() -> None:
    repo = get_repository()
    r = solve_plan(repo, _req(must_visit=frozenset({"forbidden_city"})))
    assert r.status == "solved"
    assert len(r.days) == 3
    assert r.dropped_must_visit == []
    # 所有天出发/返回在一天之内，行程时刻单调
    for day in r.days:
        assert 0 <= day.depart_min <= day.return_min < 24 * 60
        prev_leave = day.depart_min
        for s in day.stops:
            poi = repo.poi(s.poi_id)
            assert s.arrive_min >= poi.open_min, f"{s.poi_id} 早于开门"
            assert s.leave_min <= poi.close_min, f"{s.poi_id} 超过闭馆"
            assert s.leave_min - s.arrive_min == poi.duration_min
            assert s.arrive_min >= prev_leave
            prev_leave = s.leave_min
    assert "forbidden_city" in r.visited
    assert r.objective > 0


def test_daily_cap_respected() -> None:
    repo = get_repository()
    r = solve_plan(repo, _req(daily_minutes_cap=240))
    assert r.status == "solved"
    for day in r.days:
        assert day.away_minutes <= 240


def test_closed_monday_excluded_from_monday_visit() -> None:
    repo = get_repository()
    # 周一开始 1 日行程，故宫周一闭馆 → 必去则 dropped
    r = solve_plan(repo, _req(days=1, start_date=MON, must_visit=frozenset({"forbidden_city"})))
    assert r.status == "solved"
    assert r.dropped_must_visit == ["forbidden_city"]

    # 同样周一，但 2 日行程（周一+周二）→ 故宫应落在周二
    r2 = solve_plan(repo, _req(days=2, start_date=MON, must_visit=frozenset({"forbidden_city"})))
    assert r2.dropped_must_visit == []
    visited_days = [
        d.date_index for d in r2.days if any(s.poi_id == "forbidden_city" for s in d.stops)
    ]
    assert visited_days == [1], "故宫只能安排在周二"


def test_budget_forces_drop_of_pricy_pois() -> None:
    repo = get_repository()
    # 预算 200 元：门票+车费兜不住远郊长城（单程 fare ~170），必去则报不可行原因
    r = solve_plan(
        repo,
        _req(days=1, budget=200, daily_minutes_cap=600,
             must_visit=frozenset({"badaling_great_wall"})),
    )
    assert r.status == "solved"
    assert r.dropped_must_visit == ["badaling_great_wall"]


def test_no_solution_when_model_impossible() -> None:
    repo = get_repository()
    # T_day 小于任何单点"往返车程+停留"仍可求解（全 dropped）；
    # 真正的 no_solution 来自搜索失败。这里构造必去点闭馆 + 全天闭馆窗口，
    # 验证的是"不硬凑"：status=solved 但 dropped_must_visit 非空（由上层转 infeasible）
    r = solve_plan(
        repo, _req(days=1, start_date=MON, must_visit=frozenset({"national_museum"}))
    )
    assert r.status == "solved"
    assert "national_museum" in r.dropped_must_visit


def test_must_visit_all_covered_when_generous() -> None:
    repo = get_repository()
    must = frozenset({"forbidden_city", "temple_of_heaven", "summer_palace", "beijing_zoo"})
    r = solve_plan(repo, _req(days=3, budget=3000, must_visit=must))
    assert r.dropped_must_visit == []


def test_solver_deterministic_objective_same_process() -> None:
    # solution_limit=1 完全脱离时限截断波动（GLNS 在机器负载下 2s 截断点不同，
    # 会得到不同局部最优）；确定性复现是评测可断言的前提（ADR-007）
    params = SolverParams(solution_limit=1)
    repo = get_repository()
    a = solve_plan(repo, _req(), params)
    b = solve_plan(repo, _req(), params)
    assert a.objective == b.objective
    assert [d.depart_min for d in a.days] == [d.depart_min for d in b.days]


def test_time_limit_param_short() -> None:
    repo = get_repository()
    r = solve_plan(repo, _req(), SolverParams(time_limit_s=0.5))
    assert r.status == "solved"
    assert r.wall_ms >= 0
