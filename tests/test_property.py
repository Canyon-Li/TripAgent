"""属性测试（TASKS 1.8 / §15）：随机生成 POI × 约束组合，断言——

1. 求解成功且必去覆盖 ⇒ 独立校验器零冲突（求解器与校验器两套实现互相咬合）；
2. 校验器通过 ⇒ 测试内第三方 oracle 重算亦无任何硬约束违反
   （时间窗/闭馆/每日时长/预算/重复/覆盖——校验器漏报会被 oracle 抓住）；
3. 必去未覆盖 ⇒ dropped_must_visit 非空（不可行路径有明确证据，不硬凑）。

合成世界：随机 POI（窗口/时长/票价/周闭馆）+ 随机非对称耗时矩阵，
构造 DataRepository 直接喂给求解器与校验器；固定种子可复现。
"""

from __future__ import annotations

import datetime as dt
import random

from app.db.repository import POI, DataRepository, Hotel, MatrixData
from app.solver.routing import SolveRequest, SolverParams, solve_plan
from app.verifier.verify import DayClaim, StopClaim, VerifyRequest, verify_itinerary

N_SAMPLES = 120
CATEGORIES = ["history", "food", "nature", "modern", "shopping", "museum"]


def _synthetic_world(rng: random.Random) -> DataRepository:
    """随机 POI 集 + 随机非对称矩阵（fare 沿用出租车启发式口径，1 位小数）。"""
    n_pois = rng.randrange(4, 13)
    pois: list[POI] = []
    for i in range(n_pois):
        open_min = rng.randrange(5, 10) * 60 + rng.choice([0, 30])
        close_min = rng.randrange(15, 24) * 60
        duration = rng.choice([30, 45, 60, 75, 90, 120, 150, 180])
        if close_min - open_min < duration:
            close_min = min(23 * 60 + 59, open_min + duration + rng.randrange(0, 180))
        ticket = rng.choice([0, 0, 0, 2, 10, 15, 25, 30, 40, 45, 60])
        # 全周闭馆保留小概率：制造"必去点在所有天都闭馆"的不可行分支
        closed = rng.choice([[], [], [], [], [], [], [0], [1], [5, 6], [0, 1, 2, 3, 4, 5, 6]])
        pois.append(POI(
            id=f"poi_{i}",
            name=f"合成景点{i}",
            category=rng.choice(CATEGORIES),
            lat=39.9 + i * 0.01,
            lng=116.3 + i * 0.01,
            open=f"{open_min // 60:02d}:{open_min % 60:02d}",
            close=f"{close_min // 60:02d}:{close_min % 60:02d}",
            ticket=float(ticket),
            duration_min=duration,
            score=round(rng.uniform(3.0, 5.0), 1),
            closed_weekdays=closed,
        ))

    n = n_pois + 1
    minutes = [[0] * n for _ in range(n)]
    km = [[0.0] * n for _ in range(n)]
    fare = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                minutes[i][j] = rng.randrange(5, 61)           # 非对称也允许（有向矩阵）
                km[i][j] = round(minutes[i][j] * rng.uniform(0.4, 0.8), 1)
                fare[i][j] = round(max(13.0, 13.0 + 2.3 * (km[i][j] - 3.0)), 1)

    matrix = MatrixData(
        node_ids=["hotel_synth", *[p.id for p in pois]],
        minutes=minutes,
        km=km,
        fare_cny=fare,
        hotel=Hotel(id="hotel_synth", name="合成酒店", lat=39.9, lng=116.3),
        meta={"synthetic": True},
    )
    return DataRepository(pois, matrix, city="synthetic")


def _random_request(rng: random.Random, repo: DataRepository) -> SolveRequest:
    poi_ids = [p.id for p in repo.pois]
    must = frozenset(rng.sample(poi_ids, k=rng.randrange(0, min(4, len(poi_ids)) + 1)))
    return SolveRequest(
        days=rng.randrange(1, 4),
        # 低预算保留：制造预算不可行分支；高预算占多数，保证可行路径充分被检验
        budget=float(rng.choice([30, 80, 150, 300, 800, 2000, 5000, 8000, 12000])),
        daily_minutes_cap=rng.randrange(240, 661, 30),
        must_visit=must,
        preferences=frozenset(rng.sample(CATEGORIES, k=rng.randrange(0, 3))),
        start_date=dt.date(2026, 11, rng.randrange(16, 30)),
    )


def oracle_check(repo: DataRepository, req: SolveRequest, days: list) -> list[str]:
    """测试内第三方重算（与求解器、校验器均独立），返回违反描述列表。"""
    errors: list[str] = []
    visited: set[str] = set()
    spend = 0.0
    minutes = repo.matrix.minutes
    fare = repo.matrix.fare_cny

    for day in days:
        clock = day.depart_min
        prev = 0
        for s in day.stops:
            label = f"day{day.date_index}/{s.poi_id}"
            poi = repo.poi(s.poi_id)
            raw = clock + minutes[prev][s.node]
            expect_arrive = max(raw, poi.open_min)
            if s.arrive_min != expect_arrive:
                errors.append(f"{label} arrive {s.arrive_min} != 期望 {expect_arrive}")
            if s.leave_min != s.arrive_min + poi.duration_min:
                errors.append(f"{label} leave 与停留时长不符")
            if s.arrive_min < poi.open_min:
                errors.append(f"{label} 早于开门")
            if s.leave_min > poi.close_min:
                errors.append(f"{label} 晚于闭馆({poi.close_min})")
            weekday = (req.start_date + dt.timedelta(days=day.date_index)).weekday()
            if weekday in poi.closed_weekdays:
                errors.append(f"{label} 当日周{weekday}闭馆")
            if s.poi_id in visited:
                errors.append(f"{label} 重复访问")
            visited.add(s.poi_id)
            spend += poi.ticket + fare[prev][s.node]
            clock = s.leave_min
            prev = s.node
        back = clock + minutes[prev][0]
        spend += fare[prev][0]
        if back - day.depart_min > req.daily_minutes_cap:
            errors.append(
                f"day{day.date_index} 在外 {back - day.depart_min} > 上限 {req.daily_minutes_cap}"
            )

    if spend > req.budget + 1e-6:
        errors.append(f"总花费 {spend:.1f} > 预算 {req.budget}")
    for p in sorted(req.must_visit - visited):
        errors.append(f"必去未覆盖: {p}")
    return errors


def test_property_verifier_pass_implies_no_hard_violation() -> None:
    """核心属性：校验器通过 ⇒ 第三方 oracle 无硬约束违反（≥N_SAMPLES 组随机样本）。"""
    rng = random.Random(20261117)
    fast = SolverParams(time_limit_s=0.12)
    n_verified = 0

    for sample in range(N_SAMPLES):
        world = _synthetic_world(rng)
        req = _random_request(rng, world)
        result = solve_plan(world, req, fast)

        if result.status != "solved" or result.dropped_must_visit:
            # 不可行路径：必去未覆盖必须有明确证据（属性 3）
            assert result.status == "no_solution" or result.dropped_must_visit, (
                f"sample {sample}: 既不可行又无原因"
            )
            continue

        assert len(result.days) == req.days, f"sample {sample}: 天数不符"

        claims = [
            DayClaim(
                date_index=d.date_index,
                depart_min=d.depart_min,
                stops=tuple(StopClaim(s.poi_id, s.arrive_min, s.leave_min) for s in d.stops),
            )
            for d in result.days
        ]
        vreq = VerifyRequest(
            days=req.days,
            budget=req.budget,
            daily_minutes_cap=req.daily_minutes_cap,
            must_visit=req.must_visit,
            start_date=req.start_date,
        )
        verified = verify_itinerary(world, vreq, claims)

        # 属性 1：求解成功且必去覆盖 ⇒ 校验器必须零冲突
        assert verified.violations == [], (
            f"sample {sample}: 校验器冲突 {[v.type for v in verified.violations]}"
        )

        # 属性 2：校验器通过 ⇒ oracle 重算无硬约束违反
        oracle_errors = oracle_check(world, req, result.days)
        assert oracle_errors == [], f"sample {sample}: oracle 抓到违反 {oracle_errors}"
        n_verified += 1

    # 保证属性本体真正被检验到（而不是全部样本都走了不可行分支）
    assert n_verified >= N_SAMPLES * 0.5, (
        f"仅 {n_verified}/{N_SAMPLES} 个样本走到可行+校验路径，生成器需要调整"
    )
