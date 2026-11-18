"""能力心跳健康检查（§7.4：30 秒心跳、失败自动摘除、恢复自动回切）。

分发依据是**卡内 healthcheck 字段的 type**（§9 DDL 该列的用途本体）——动态注册
的卡只要用已知 type 即自动纳入心跳，不依赖代码里按 (name, version) 硬编码。

检查全部**确定性、不外呼**：solver = 微缩自检求解；matrix = 数据集加载；
verifier = 已知空行程校验；llm_adapter = 配置在场（CI 哨兵 key 下如实显示
unhealthy——与 chat 主链路「无 LLM 则降级」的行为一致）。

心跳状态（last_checked）存进程内存：§9 DDL 无对应列，不漂移；
持久化的摘除事实落在 capabilities.status（unhealthy），重启后由首次心跳复核。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable

from app.config import get_settings
from app.db.repository import get_repository
from app.llm.adapter import llm_available
from app.registry import service
from app.registry.cards import CapabilityCard
from app.registry.seed import SOLVER_VERSIONS
from app.solver.routing import SolveRequest, solve_plan
from app.verifier.verify import DayClaim, VerifyRequest, verify_itinerary

CheckFn = Callable[[], None]
# healthcheck.type -> 检查工厂（按卡构造，便于携带 version 等卡内参数）
HealthcheckFactory = Callable[[CapabilityCard], CheckFn]


def heartbeat_interval_s() -> float:
    """§7.4 默认 30s；可经 REGISTRY_HEARTBEAT_INTERVAL_S 覆盖（测试停表防竞态）。"""
    return get_settings().registry_heartbeat_interval_s


def solver_check(card: CapabilityCard) -> CheckFn:
    """微缩自检：真实数据集 1 日无必去求解必须 solved（全链路 ~ms 级）。"""
    params = SOLVER_VERSIONS.get(card.version)
    if params is None:
        # 与路由口径一致：卡存在但参数未收录 = 配置不完整，视为不健康
        raise RuntimeError(f"solver@{card.version} 无参数绑定（SOLVER_VERSIONS 未收录）")

    def _check() -> None:
        repo = get_repository()
        req = SolveRequest(
            days=1, budget=5000, daily_minutes_cap=480,
            must_visit=frozenset(), preferences=frozenset(),
            start_date=dt.date.today(),
        )
        result = solve_plan(repo, req, params)
        if result.status != "solved":
            raise RuntimeError(f"self-test solve status={result.status}")

    return _check


def matrix_check(_card: CapabilityCard) -> CheckFn:
    def _check() -> None:
        repo = get_repository()  # 加载即校验（pydantic 严格 schema + 矩阵一致性）
        if not repo.pois:
            raise RuntimeError("repository loaded but has no POIs")

    return _check


def verifier_check(_card: CapabilityCard) -> CheckFn:
    """已知用例：空行程 + 宽约束必须零冲突（校验器能跑且不误报）。"""
    def _check() -> None:
        repo = get_repository()
        result = verify_itinerary(
            repo,
            VerifyRequest(
                days=1, budget=10_000, daily_minutes_cap=480,
                must_visit=frozenset(), start_date=dt.date.today(),
            ),
            [DayClaim(date_index=0, depart_min=540, stops=())],
        )
        if result.violations:
            raise RuntimeError(
                f"known-good case reported: {[v.type for v in result.violations]}")

    return _check


def llm_check(_card: CapabilityCard) -> CheckFn:
    def _check() -> None:
        if not llm_available():
            raise RuntimeError("llm adapter not configured (key/base_url 缺失或为测试哨兵)")

    return _check


HEALTHCHECK_BINDINGS: dict[str, HealthcheckFactory] = {
    "self_test_solve": solver_check,
    "repository_load": matrix_check,
    "known_case_verify": verifier_check,
    "config_present": llm_check,
}

# ref(name@version) -> {checked_at, ok, detail}；进程内存，重启即空
_last_checked: dict[str, dict] = {}


def run_heartbeat() -> dict[str, dict]:
    """跑一轮全部非 retired 卡的健康检查，摘除 / 回切 status。

    返回 {ref: {checked_at, ok, detail, status}}——/api/registry/health 的数据源。
    """
    now = dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")
    results: dict[str, dict] = {}
    for card in service.list_cards(include_retired=False):
        hc_type = card.healthcheck.get("type")
        factory = HEALTHCHECK_BINDINGS.get(hc_type) if isinstance(hc_type, str) else None
        if factory is None:
            results[card.ref] = {
                "checked_at": now, "ok": None,
                "detail": f"unknown healthcheck type: {card.healthcheck.get('type')}",
                "status": card.status,
            }
            continue
        try:
            factory(card)()
            ok, detail = True, ""
        except Exception as e:  # noqa: BLE001 —— 心跳把失败原因原样带回
            ok, detail = False, f"error: {e}"
        # 失败摘除（active→unhealthy）；成功回切（unhealthy→active）；retired 不动
        if not ok and card.status == "active":
            card = service.set_status(card.name, card.version, "unhealthy") or card
        elif ok and card.status == "unhealthy":
            card = service.set_status(card.name, card.version, "active") or card
        entry = {"checked_at": now, "ok": ok, "detail": detail, "status": card.status}
        _last_checked[card.ref] = entry
        results[card.ref] = entry
    return results


def health_status() -> dict[str, dict]:
    """当前各能力心跳状态（DB status + 内存里最近一次检查结果）。"""
    cards = service.list_cards(include_retired=True)
    return {
        c.ref: _last_checked.get(c.ref, {
            "checked_at": None, "ok": None, "detail": "尚未心跳", "status": c.status,
        })
        for c in cards
    }


async def heartbeat_loop() -> None:
    """lifespan 常驻任务：按间隔心跳（DB/求解为同步 IO，放线程池避免阻塞事件循环）。"""
    while True:
        await asyncio.to_thread(run_heartbeat)
        await asyncio.sleep(heartbeat_interval_s())
