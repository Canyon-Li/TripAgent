"""默认能力卡与 solver 版本参数（ADR-007：v1/v2 差异收敛在 SolverParams 层）。

§7.1 发现链三件套（solver / matrix / verifier）+ LLM 适配器（B3 可插拔）。
matrix 归入 §9 kind 枚举的 "tool" 桶（§7.6：运行时侧 capability 调用执行
POI 查询 / 距离矩阵 / 求解 / 校验——除 solver/verifier 外的固定能力均为 tool）。
"""

from __future__ import annotations

from app.registry.cards import CapabilityCard
from app.solver.routing import SolverParams

# ---- solver 双版本（ADR-007 / B11）------------------------------------------
# v1：TASKS 1.2 的现行为（均衡目标：交通时间主导，花费次要权重）
# v2：灰度载体——高分优先、弱化交通/花费权重（取舍策略不同，接口与代码路径相同）
# 默认 v2 权重 0：阶段 3 评测基线零漂移；灰度 = 改 weight，回滚 = 改回 0（不重启）
SOLVER_VERSIONS: dict[str, SolverParams] = {
    "v1": SolverParams(),
    "v2": SolverParams(alpha=0.7, beta=0.05, w_score=150.0, pref_boost=1.0),
}

_SOLVE_IN = {"type": "object", "fields": [
    "days", "budget", "daily_minutes_cap", "must_visit", "preferences", "start_date",
]}
_SOLVE_OUT = {"type": "object", "fields": [
    "status", "days", "visited", "dropped_must_visit", "objective", "phases",
]}

DEFAULT_CARDS: list[CapabilityCard] = [
    CapabilityCard(
        name="solver", version="v1", kind="solver",
        input_schema=_SOLVE_IN, output_schema=_SOLVE_OUT,
        endpoint="inprocess://app.solver.routing.solve_plan#v1",
        healthcheck={"type": "self_test_solve"},
        cost_estimate=2.0, tags=["planning", "vrptw", "balanced"],
        status="active", traffic_weight=100,
    ),
    CapabilityCard(
        name="solver", version="v2", kind="solver",
        input_schema=_SOLVE_IN, output_schema=_SOLVE_OUT,
        endpoint="inprocess://app.solver.routing.solve_plan#v2",
        healthcheck={"type": "self_test_solve"},
        cost_estimate=2.0, tags=["planning", "vrptw", "score-first"],
        status="active", traffic_weight=0,
    ),
    CapabilityCard(
        name="matrix", version="static", kind="tool",
        input_schema={"type": "object", "fields": ["poi_id", "from_node", "to_node"]},
        output_schema={"type": "object", "fields": ["minutes", "km", "fare_cny"]},
        endpoint="inprocess://app.db.repository.get_repository",
        healthcheck={"type": "repository_load"},
        cost_estimate=None, tags=["matrix", "distance", "static"],
        status="active", traffic_weight=100,
    ),
    CapabilityCard(
        name="verifier", version="v1", kind="verifier",
        input_schema={"type": "object", "fields": [
            "days", "budget", "daily_minutes_cap", "must_visit", "start_date", "claims",
        ]},
        output_schema={"type": "object", "fields": ["violations", "recomputed_budget"]},
        endpoint="inprocess://app.verifier.verify.verify_itinerary",
        healthcheck={"type": "known_case_verify"},
        cost_estimate=None, tags=["verification"],
        status="active", traffic_weight=100,
    ),
    CapabilityCard(
        name="llm_adapter", version="deepseek", kind="llm_adapter",
        input_schema={"type": "object", "fields": ["messages", "tools"]},
        output_schema={"type": "object", "fields": ["content", "tool_calls", "usage"]},
        endpoint="https://api.deepseek.com（OpenAI-compatible，B3 可插拔）",
        healthcheck={"type": "config_present"},
        cost_estimate=None, tags=["llm", "deepseek"],
        status="active", traffic_weight=100,
    ),
]
