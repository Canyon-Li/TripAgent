"""Registry API（§10）：能力卡查询/注册（upsert）+ 各能力心跳状态。

POST 既是注册通道也是**回滚通道**——对已有 (name, version) upsert 改
traffic_weight / status，下一次 /api/plan 即生效，无需重启（§7.4）。
weight / deregister 两条子路径是 §10 之外的窄更新辅助端点（只动一个字段，
避免整卡 upsert 误覆写；同 /api/pois 先例，已在 project.md §10 注记）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.registry import health as registry_health
from app.registry import service as registry_service
from app.registry.cards import CapabilityCard

router = APIRouter(prefix="/api/registry")


class WeightUpdate(BaseModel):
    weight: int = Field(ge=0, le=10_000)


@router.get("/capabilities")
def list_capabilities(
    kind: str | None = None,
    tag: str | None = None,
    latest: bool = False,
    include_retired: bool = True,
) -> dict:
    """能力卡列表；kind/tag/latest 为 §7.4 发现参数（latest=每 name 取最高版本）。"""
    cards = registry_service.list_cards(include_retired=include_retired)
    if kind:
        cards = [c for c in cards if c.kind == kind]
    if tag:
        cards = [c for c in cards if tag in c.tags]
    if latest:
        by_name: dict[str, CapabilityCard] = {}
        for c in cards:
            cur = by_name.get(c.name)
            newer = cur is None or (
                registry_service.version_key(c.version)
                > registry_service.version_key(cur.version))
            if newer:
                by_name[c.name] = c
        cards = list(by_name.values())
    names = sorted({c.name for c in cards})
    return {
        "count": len(cards),
        "capabilities": [c.model_dump() for c in cards],
        # §7.4 版本策略视图：pinned | canary | off + latest 版本号（按当前权重判定）
        "policies": [registry_service.policy_view(n) for n in names],
    }


@router.post("/capabilities", response_model=CapabilityCard)
def upsert_capability(card: CapabilityCard) -> CapabilityCard:
    """注册 / 更新能力卡（upsert；回滚通道——改权重即改路由绑定）。"""
    return registry_service.register(card)


@router.post("/capabilities/{name}/{version}/weight")
def update_weight(name: str, version: str, body: WeightUpdate) -> dict:
    """灰度/回滚便捷端点：改一行权重（§7.4「回滚只需更新一行记录」）。"""
    updated = registry_service.set_weight(name, version, body.weight)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"capability not found: {name}@{version}")
    return updated.model_dump()


@router.post("/capabilities/{name}/{version}/deregister")
def deregister_capability(name: str, version: str) -> dict:
    """注销（status→retired 归档，不物理删除）。"""
    card = registry_service.deregister(name, version)
    if card is None:
        raise HTTPException(status_code=404, detail=f"capability not found: {name}@{version}")
    return card.model_dump()


@router.get("/health")
def capabilities_health() -> dict:
    """各能力心跳状态（最近一跳结果 + 当前 status；绑定按卡内 healthcheck.type 分发）。"""
    status = registry_health.health_status()
    return {
        "interval_s": registry_health.heartbeat_interval_s(),
        "capabilities": status,
        "summary": {
            "healthy": sum(1 for v in status.values() if v["ok"] is True),
            "unhealthy": sum(1 for v in status.values() if v["ok"] is False),
            "unknown": sum(1 for v in status.values() if v["ok"] is None),
        },
    }
