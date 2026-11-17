"""Solver v1：OR-Tools Routing VRPTW 建模（§8 / ADR-002，TASKS 1.2）。

模型（m 天 = m 辆车，酒店为 depot，约定 matrix 节点 0 = 酒店锚点）：
- 时间维度用**绝对时刻**（距午夜分钟数）建模：transit(i,j) = 停留_i + minutes[i][j]，
  每个景点的到达时刻约束 `CumulVar(node).SetRange(open_i, close_i - duration_i)`，
  早到由 slack 吸收为等待。slack_max/capacity 取 24h；
  每日时长上限 T_day 不用 capacity 表达（那会错切成"午夜前 T_day 分钟"），
  而是对每辆车加 `CumulVar(End) - CumulVar(Start) <= T_day` 的 span 约束——
  与 §8 的意图一致（每日在外时长不超上限），实现上是绝对时刻 VRPTW 的标准写法。
- 预算维度：transit(i,j) = 门票_j + fare[i][j]（角为单位取整，与数据 1 位小数精确对齐，
  容量取 floor(B*10) 只收紧不放松），`AddDimensionWithVehicleCapacity` 每车 ≤ B，
  另加 Σ各车 End ≤ B 保证**总预算**跨天口径。
- 景点取舍：`AddDisjunction([node], penalty)`；penalty = w_score*score（偏好类目乘
  1+boost），必去点取超大 penalty 强制优先纳入。
- 周闭馆：`VehicleVar(node).RemoveValue(v)` 把闭馆当天的车从可选集合剔除；
  全部天闭馆的景点自然落为 dropped（若在必去清单则进入不可行原因）。
- 目标（§8）：min Σ α·minutes + β·(门票+车费) + Σ 未访问惩罚。

两阶段求解（必去点覆盖的搜索保证）：
1. 完整目标求解（所有节点带价值 penalty）；
2. 若必去点被丢弃（时间/预算挤压 + 局部搜索未逃出），用"可选点 penalty=0"的救援
   模型重解（等价于只保必去，极易求解），覆盖后经 `ReadAssignmentFromRoutes` 回灌
   完整模型 warm-start，恢复可选点；救援也覆盖不了 → 返回救援结果的 dropped 清单，
   由上层按不可行处理（TASKS 1.5，不硬凑）。

输出抽取：只取每辆车的**访问序列 + 有效出发时刻**，时间线（arrive/leave/wait）按
"早到则原地等待"语义重走——语义与独立校验器对齐，校验器仍独立实现（ADR-001）。

确定性说明：first-solution 确定；局部搜索（GLS）受 time_limit 截断，跨机器可能得到
不同但同等可行的方案。评测断言约束性质（可行性/覆盖/预算），不锁具体序列（§15）。
"""

from __future__ import annotations

import datetime as dt
import time as _time
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.db.repository import POI, DataRepository

# 预算维度取整单位：1 角（fare/ticket 数据均不超过 1 位小数，×10 后精确无损）
_MONEY_SCALE = 10
_DAY_MINUTES = 24 * 60
_DEFAULT_FIRST_SOLUTION = "PARALLEL_CHEAPEST_INSERTION"


@dataclass(frozen=True)
class SolverParams:
    """v1 目标权重与运行参数（v2 将在此层参数化，ADR-007）。"""

    alpha: float = 1.0              # 交通时间权重（分钟）
    beta: float = 0.1               # 花费权重（元 → 分钟量级归一）
    w_score: float = 100.0          # drop penalty = w_score * score
    pref_boost: float = 0.5         # 偏好类目 penalty 增益
    must_penalty: int = 10_000_000  # 必去点惩罚（覆盖兜底由两阶段保证）
    vehicle_fixed_cost: int = 0     # 启用一天（一辆车）的固定成本，§8 SetFixedCostOfVehicle
    time_limit_s: float = 2.0       # 求解时限（§14：n≤30，防交互卡顿）
    first_solution: str = _DEFAULT_FIRST_SOLUTION  # 首解策略（枚举成员名）


@dataclass(frozen=True)
class SolveRequest:
    """求解输入（与 API schema 解耦，便于属性测试构造随机组合）。"""

    days: int
    budget: float
    daily_minutes_cap: int
    must_visit: frozenset[str]
    preferences: frozenset[str]
    start_date: dt.date


@dataclass
class StopPlan:
    node: int          # 矩阵下标（0=酒店）
    poi_id: str
    arrive_min: int    # 进入时刻（早到等待之后）
    leave_min: int
    wait_min: int


@dataclass
class DayPlan:
    date_index: int
    date: dt.date
    depart_min: int    # 离开酒店时刻
    return_min: int    # 回到酒店时刻
    stops: list[StopPlan] = field(default_factory=list)

    @property
    def away_minutes(self) -> int:
        return self.return_min - self.depart_min


@dataclass
class SolveResult:
    status: str                       # solved | no_solution
    days: list[DayPlan] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    dropped_must_visit: list[str] = field(default_factory=list)  # 未覆盖的必去点
    objective: int = 0
    wall_ms: int = 0
    solver_status: str = ""           # OR-Tools 原始状态名（最后阶段）
    phases: list[str] = field(default_factory=list)  # 实际走过的求解阶段（可观测性）


def solve_plan(
    repo: DataRepository, req: SolveRequest, params: SolverParams | None = None
) -> SolveResult:
    """入口：结构化约束 → VRPTW 求解。不抛业务异常，不可行由调用方按结果判定。"""
    p = params or SolverParams()
    t0 = _time.perf_counter()
    phases: list[str] = []

    routing, manager, pois_ordered, minutes = _build_model(repo, req, p, optional_penalties=True)
    search = _search_params(p)
    assignment = routing.SolveWithParameters(search)
    result = _finalize(routing, assignment, manager, pois_ordered, minutes, req, t0, ["full"])
    if assignment is None or not result.dropped_must_visit:
        return result

    # ---- 阶段 2：救援模型（可选点 penalty=0，等价只保必去）----
    phases.append("rescue")
    res_routing, res_manager, res_pois, res_minutes = _build_model(
        repo, req, p, optional_penalties=False
    )
    res_assignment = res_routing.SolveWithParameters(search)
    rescue = _finalize(
        res_routing, res_assignment, res_manager, res_pois, res_minutes, req, t0, phases
    )
    if res_assignment is None or rescue.dropped_must_visit:
        # 必去点确实放不下（时间窗/预算/闭馆挤压）：如实返回不可行证据
        return rescue

    # ---- 阶段 3：救援路线回灌完整模型 warm-start，恢复可选点 ----
    phases.append("warm_start")
    routes = [[s.node for s in day.stops] for day in rescue.days]
    initial = routing.ReadAssignmentFromRoutes(routes, ignore_inactive_indices=True)
    if initial is not None:
        warm = routing.SolveFromAssignmentWithParameters(initial, search)
        warm_result = _finalize(routing, warm, manager, pois_ordered, minutes, req, t0, phases)
        if warm is not None and not warm_result.dropped_must_visit:
            return warm_result
    # warm-start 失败或又丢必去：救援结果必去已覆盖，虽稀疏但可行、诚实
    rescue.phases = phases
    rescue.wall_ms = int((_time.perf_counter() - t0) * 1000)
    return rescue


def _build_model(
    repo: DataRepository,
    req: SolveRequest,
    p: SolverParams,
    optional_penalties: bool,
) -> tuple[pywrapcp.RoutingModel, pywrapcp.RoutingIndexManager, list[POI | None], list[list[int]]]:
    """建 VRPTW 模型。optional_penalties=False 时可选点 penalty=0（救援模式）。"""
    pois_ordered: list[POI | None] = repo.pois_in_matrix_order()  # [酒店, poi...]
    n = len(pois_ordered)
    m = req.days
    minutes = repo.matrix.minutes
    fare = repo.matrix.fare_cny

    manager = pywrapcp.RoutingIndexManager(n, m, 0)  # 节点 0 = 酒店锚点（repository 保证）
    routing = pywrapcp.RoutingModel(manager)

    # ---- 时间回调：停留时间计入离开节点一侧（回调参数是 routing index，
    #      必须先转矩阵节点下标，二者仅在单车 depot 情形部分重合）----
    def time_cb(from_index: int, to_index: int) -> int:
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        service = pois_ordered[i].duration_min if pois_ordered[i] else 0
        return service + minutes[i][j]

    time_cb_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_cb_idx,
        slack_max=_DAY_MINUTES,
        capacity=_DAY_MINUTES,
        fix_start_cumul_to_zero=False,
        name="Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # ---- 预算回调：门票(目的地) + 车费，角为单位 ----
    def money_cb(from_index: int, to_index: int) -> int:
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        ticket = round(pois_ordered[j].ticket * _MONEY_SCALE) if pois_ordered[j] else 0
        return ticket + round(fare[i][j] * _MONEY_SCALE)

    money_cb_idx = routing.RegisterTransitCallback(money_cb)
    budget_cap = int(req.budget * _MONEY_SCALE)  # floor：宁可更严，不放松
    routing.AddDimensionWithVehicleCapacity(
        money_cb_idx,
        slack_max=0,
        vehicle_capacities=[budget_cap] * m,
        fix_start_cumul_to_zero=True,
        name="Budget",
    )
    budget_dim = routing.GetDimensionOrDie("Budget")

    solver = routing.solver()

    # 每日时长上限：span 约束（见模块 docstring）
    for v in range(m):
        solver.Add(
            time_dim.CumulVar(routing.End(v)) - time_dim.CumulVar(routing.Start(v))
            <= req.daily_minutes_cap
        )
    # 总预算：各车 End 累计之和 ≤ B（跨天总口径）
    solver.Add(solver.Sum(budget_dim.CumulVar(routing.End(v)) for v in range(m)) <= budget_cap)

    # fix_start_cumul_to_zero=False 时按惯例注册 finalizer，
    # 让局部搜索在优化路线的同时收拢出发/返回时刻
    for v in range(m):
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v)))
        routing.AddVariableMaximizedByFinalizer(time_dim.CumulVar(routing.End(v)))

    # ---- 节点约束：时间窗 / 周闭馆 / disjunction ----
    weekday_of_day = [(req.start_date + dt.timedelta(days=v)).weekday() for v in range(m)]
    for node_idx in range(1, n):
        poi = pois_ordered[node_idx]
        assert poi is not None
        index = manager.NodeToIndex(node_idx)
        time_dim.CumulVar(index).SetRange(poi.open_min, poi.close_min - poi.duration_min)

        for v in range(m):
            if weekday_of_day[v] in poi.closed_weekdays:
                routing.VehicleVar(index).RemoveValue(v)

        if poi.id in req.must_visit:
            penalty = p.must_penalty
        elif optional_penalties:
            boost = 1 + (p.pref_boost if poi.category in req.preferences else 0)
            penalty = int(p.w_score * poi.score * boost)
        else:
            penalty = 0  # 救援模式：可选点免费丢弃，只保必去
        routing.AddDisjunction([index], penalty)

    # ---- 目标：arc cost = α·时间 + β·(门票+车费)；未访问节点付出 disjunction 惩罚 ----
    def arc_cost_cb(from_index: int, to_index: int) -> int:
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        ticket = pois_ordered[j].ticket if pois_ordered[j] else 0.0
        return int(round(p.alpha * minutes[i][j] + p.beta * (ticket + fare[i][j])))

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cost_cb))
    if p.vehicle_fixed_cost:
        for v in range(m):
            routing.SetFixedCostOfVehicle(p.vehicle_fixed_cost, v)

    return routing, manager, pois_ordered, minutes


def _search_params(p: SolverParams) -> pywrapcp.DefaultRoutingSearchParameters:
    search = pywrapcp.DefaultRoutingSearchParameters()
    strategy_enum = routing_enums_pb2.FirstSolutionStrategy
    search.first_solution_strategy = getattr(
        strategy_enum, p.first_solution, getattr(strategy_enum, _DEFAULT_FIRST_SOLUTION)
    )
    search.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search.time_limit.FromMilliseconds(int(p.time_limit_s * 1000))
    return search


def _finalize(
    routing: pywrapcp.RoutingModel,
    assignment: pywrapcp.Assignment | None,
    manager: pywrapcp.RoutingIndexManager,
    pois_ordered: list[POI | None],
    minutes: list[list[int]],
    req: SolveRequest,
    t0: float,
    phases: list[str],
) -> SolveResult:
    """把一次求解的 assignment 收敛为 SolveResult（无解 → no_solution）。"""
    wall_ms = int((_time.perf_counter() - t0) * 1000)
    if assignment is None:
        return SolveResult(
            status="no_solution",
            wall_ms=wall_ms,
            solver_status=routing_enums_pb2.RoutingSearchStatus.Value.Name(routing.status()),
            phases=list(phases),
        )
    result = _extract(routing, assignment, manager, pois_ordered, minutes, req)
    result.objective = int(assignment.ObjectiveValue())
    result.wall_ms = wall_ms
    result.solver_status = routing_enums_pb2.RoutingSearchStatus.Value.Name(routing.status())
    result.phases = list(phases)
    return result


def _extract(
    routing: pywrapcp.RoutingModel,
    assignment: pywrapcp.Assignment,
    manager: pywrapcp.RoutingIndexManager,
    pois_ordered: list[POI | None],
    minutes: list[list[int]],
    req: SolveRequest,
) -> SolveResult:
    """抽取每辆车的访问序列与出发时刻，时间线按"早到等待"语义重走。"""
    result = SolveResult(status="solved")
    visited: set[str] = set()
    time_dim = routing.GetDimensionOrDie("Time")

    for v in range(req.days):
        day = DayPlan(
            date_index=v,
            date=req.start_date + dt.timedelta(days=v),
            depart_min=0,
            return_min=0,
        )
        seq: list[int] = []  # 矩阵下标序列（不含酒店）
        index = routing.Start(v)
        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            if node_idx != 0:
                seq.append(node_idx)
            index = assignment.Value(routing.NextVar(index))

        if not seq:
            day.depart_min = assignment.Value(time_dim.CumulVar(routing.Start(v)))
            day.return_min = day.depart_min
            result.days.append(day)
            continue

        # 有效出发时刻 = 首站到达 cumul − 酒店→首站车程
        first = seq[0]
        first_cumul = assignment.Value(time_dim.CumulVar(manager.NodeToIndex(first)))
        day.depart_min = max(0, first_cumul - minutes[0][first])

        t, prev = day.depart_min, 0
        for node in seq:
            poi = pois_ordered[node]
            assert poi is not None
            raw = t + minutes[prev][node]
            arrive = max(raw, poi.open_min)
            day.stops.append(
                StopPlan(
                    node=node,
                    poi_id=poi.id,
                    arrive_min=arrive,
                    leave_min=arrive + poi.duration_min,
                    wait_min=arrive - raw,
                )
            )
            visited.add(poi.id)
            t = arrive + poi.duration_min
            prev = node
        day.return_min = t + minutes[prev][0]
        result.days.append(day)

    result.visited = visited
    result.dropped_must_visit = sorted(req.must_visit - visited)
    return result
