"""独立校验器（TASKS 1.3 / ADR-001）：从行程路线逐段重算，输出 violations[]。

设计约束：
- **不复用求解器代码路径**：本模块不 import 任何 app.solver 内容，输入是纯
  dict/dataclass（由 API 层从求解结果转换），数据只经 repository 读取——
  两套实现对同一事实各自计算，正是捕获建模错误的手段（§8）。
- 重算口径：
  * 时间线：给定每日出发时刻，逐段 raw = 离开上一站 + 车程；进入 = max(raw, open)
    （早到=原地等待）；离开 = 进入 + 建议停留。声明时刻与重算不一致 → timeline_mismatch。
  * 闭馆：离开 > close（含停留无法在闭馆前完成）→ window_closed。
  * 周闭馆：到访日 weekday ∈ closed_weekdays → closed_weekday。
  * 重复访问 / 未知 POI / 非法日下标 → 各自 violation。
  * 每日在外时长 = 回酒店 − 出酒店 ≤ daily_minutes_cap → daily_cap_exceeded。
  * 预算 = Σ门票(到访) + Σ车费(全程含酒店往返) ≤ budget → budget_exceeded
    （浮点容差 1e-6，金额数据粒度 0.1 元）。
- 校验器只判定、不修改、不重排——修正是求解器的事（§7.1 降级重试在后续阶段）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.db.repository import DataRepository

# 金额比较容差：数据粒度 0.1 元，浮点累加误差远小于此
_EPS = 1e-6


@dataclass(frozen=True)
class StopClaim:
    """校验器视角的一站声明（由 API 层从求解结果转换而来）。"""

    poi_id: str
    arrive_min: int
    leave_min: int


@dataclass(frozen=True)
class DayClaim:
    date_index: int
    depart_min: int
    stops: tuple[StopClaim, ...]


@dataclass(frozen=True)
class VerifyRequest:
    """校验输入的约束侧（与求解输入同口径）。"""

    days: int
    budget: float
    daily_minutes_cap: int
    must_visit: frozenset[str]
    start_date: dt.date


@dataclass
class Violation:
    type: str
    detail: str
    day_index: int | None = None
    poi: str | None = None


@dataclass
class DayAudit:
    """单日费用审计（阶段 5 前置修复：拆分让用户能对平——门票 + 车费 = 当日小计）。"""

    date_index: int
    away_minutes: int
    fare_cny: float            # 当日车费（出租车启发式，含酒店往返）
    ticket_cny: float = 0.0    # 当日门票小计


@dataclass
class VerificationResult:
    violations: list[Violation] = field(default_factory=list)
    recomputed_budget: float = 0.0
    tickets_cny: float = 0.0   # 全程门票小计（拆分可见，data/badcases.jsonl 首条）
    fare_cny: float = 0.0      # 全程车费小计（含每日酒店往返）
    day_audits: list[DayAudit] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def types(self) -> set[str]:
        return {v.type for v in self.violations}


def verify_itinerary(
    repo: DataRepository, request: VerifyRequest, days: list[DayClaim]
) -> VerificationResult:
    """对一份已生成的行程做独立全量复核。"""
    result = VerificationResult()
    minutes = repo.matrix.minutes
    fare = repo.matrix.fare_cny
    total_cost = 0.0
    seen: set[str] = set()

    for day in days:
        if not 0 <= day.date_index < request.days:
            result.violations.append(Violation(
                "invalid_day_index",
                f"day_index {day.date_index} 超出天数 {request.days}",
                day_index=day.date_index,
            ))
            continue

        # ---- 该日逐段重算时间线与费用（门票/车费分账累计，总额不变）----
        fare_cost = 0.0
        ticket_cost = 0.0
        t = day.depart_min
        prev = 0  # 矩阵下标 0 = 酒店
        for stop in day.stops:
            try:
                node = repo.matrix.index_of(stop.poi_id)
                poi = repo.poi(stop.poi_id)
            except KeyError:
                result.violations.append(Violation(
                    "unknown_poi", f"未知 POI: {stop.poi_id}",
                    day_index=day.date_index, poi=stop.poi_id,
                ))
                continue

            if stop.poi_id in seen:
                result.violations.append(Violation(
                    "duplicate_visit", f"POI 被重复访问: {stop.poi_id}",
                    day_index=day.date_index, poi=stop.poi_id,
                ))
            seen.add(stop.poi_id)

            visit_date = request.start_date + dt.timedelta(days=day.date_index)
            if visit_date.weekday() in poi.closed_weekdays:
                result.violations.append(Violation(
                    "closed_weekday",
                    f"{poi.name}({stop.poi_id}) 在 {visit_date.isoformat()} "
                    f"(周{'一二三四五六日'[visit_date.weekday()]}) 闭馆",
                    day_index=day.date_index, poi=stop.poi_id,
                ))

            raw = t + minutes[prev][node]
            arrive = max(raw, poi.open_min)
            leave = arrive + poi.duration_min
            if arrive != stop.arrive_min or leave != stop.leave_min:
                result.violations.append(Violation(
                    "timeline_mismatch",
                    f"{stop.poi_id}: 声明 [{stop.arrive_min},{stop.leave_min}] "
                    f"vs 重算 [{arrive},{leave}]",
                    day_index=day.date_index, poi=stop.poi_id,
                ))
            if leave > poi.close_min:
                result.violations.append(Violation(
                    "window_closed",
                    f"{poi.name}({stop.poi_id}) 离开 {leave}min 超过闭馆 {poi.close_min}min",
                    day_index=day.date_index, poi=stop.poi_id,
                ))

            fare_cost += fare[prev][node]
            ticket_cost += poi.ticket
            t = arrive + poi.duration_min
            prev = node

        return_min = t + minutes[prev][0]
        fare_cost += fare[prev][0]
        away = return_min - day.depart_min
        if away > request.daily_minutes_cap:
            result.violations.append(Violation(
                "daily_cap_exceeded",
                f"第 {day.date_index + 1} 天在外 {away}min 超上限 "
                f"{request.daily_minutes_cap}min",
                day_index=day.date_index,
            ))
        total_cost += fare_cost + ticket_cost
        result.day_audits.append(DayAudit(
            day.date_index, away, round(fare_cost, 2), round(ticket_cost, 2),
        ))

    if total_cost > request.budget + _EPS:
        result.violations.append(Violation(
            "budget_exceeded",
            f"重算总花费 {total_cost:.1f} 元超预算 {request.budget:g} 元",
        ))

    for poi_id in sorted(request.must_visit - seen):
        result.violations.append(Violation(
            "must_visit_missed", f"必去点未被访问: {poi_id}", poi=poi_id,
        ))

    result.recomputed_budget = round(total_cost, 2)
    # 拆分口径 = Σ逐日(先 round) 再 round：与响应里的逐日小计严格同源，
    # 「逐日加总 == 全程拆分 == 总花费」是结构保证而非数据巧合（对平判据）
    result.tickets_cny = round(sum(a.ticket_cny for a in result.day_audits), 2)
    result.fare_cny = round(sum(a.fare_cny for a in result.day_audits), 2)
    return result
