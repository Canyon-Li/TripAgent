"""Registry 服务（§7.4）：注册 / 注销 / 发现 / traffic_weight 加权路由。

「回滚不重启」的实现本体：权重与版本绑定存 SQLite，route() 每次读取——
改一行 traffic_weight，下一次请求即生效，无需重启进程（§7.4 / §11.4）。

路由确定性（ADR-007「灰度效果可用确定性评测集直接断言」）：
stable_hash(trace_id) % Σweights 落入的累计区间选卡——同一 trace 恒选同一版本，
(100,0) → 全 v1、(0,100) → 全 v2、混合 → 按比例两版可见，且与请求顺序无关。
"""

from __future__ import annotations

import hashlib
import json
from typing import cast

from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models import Capability
from app.registry.cards import CapabilityCard, Kind, Status
from app.registry.seed import DEFAULT_CARDS


class CapabilityUnavailableError(RuntimeError):
    """某 kind 无可路由能力（未注册 / 全部摘除 / 权重全 0）。调用方按 503 处理，不硬凑。"""


def _row_to_card(row: Capability) -> CapabilityCard:
    return CapabilityCard(
        name=row.name,
        version=row.version,
        # DB 无枚举约束，读侧收窄到 Literal（写入侧经 pydantic 校验）
        kind=cast(Kind, row.kind),
        input_schema=json.loads(row.input_schema),
        output_schema=json.loads(row.output_schema),
        endpoint=row.endpoint,
        healthcheck=json.loads(row.healthcheck),
        cost_estimate=row.cost_estimate,
        tags=[t for t in row.tags.split(",") if t],
        status=cast(Status, row.status),
        traffic_weight=row.traffic_weight,
    )


def version_key(version: str) -> tuple:
    """版本排序键：纯数字段按数值比较（v1<v2<v10），否则回退字符串档。"""
    parts = version.split(".")
    if parts and all(p.isdigit() for p in parts):
        return (0, [int(p) for p in parts], "")
    return (1, [0], version)


def ensure_seed() -> None:
    """缺行补插（幂等）：新库 / 迁移后首访时填入默认能力卡。

    只插入缺失的 (name, version)，绝不覆写已有行——演示中改过的权重 / 状态得以保留。
    """
    with SessionLocal() as db:
        existing = {(name, version) for name, version in db.execute(
            select(Capability.name, Capability.version)
        ).all()}
        for card in DEFAULT_CARDS:
            if (card.name, card.version) in existing:
                continue
            db.add(Capability(
                name=card.name,
                version=card.version,
                kind=card.kind,
                input_schema=json.dumps(card.input_schema, ensure_ascii=False),
                output_schema=json.dumps(card.output_schema, ensure_ascii=False),
                endpoint=card.endpoint,
                healthcheck=json.dumps(card.healthcheck, ensure_ascii=False),
                cost_estimate=card.cost_estimate,
                tags=",".join(card.tags),
                status=card.status,
                traffic_weight=card.traffic_weight,
            ))
        db.commit()


def _maybe_seed_if_empty() -> None:
    """表全空（新进程首访新库）才补种；被摘除 / 注销的行不属于「空表」，不会误复活。"""
    with SessionLocal() as db:
        n = db.scalar(select(Capability.name).limit(1))
    if n is None:
        ensure_seed()


def get_card(name: str, version: str) -> CapabilityCard | None:
    with SessionLocal() as db:
        row = db.get(Capability, (name, version))
        return _row_to_card(row) if row else None


def list_cards(include_retired: bool = True) -> list[CapabilityCard]:
    _maybe_seed_if_empty()
    with SessionLocal() as db:
        stmt = select(Capability).order_by(Capability.kind, Capability.name, Capability.version)
        if not include_retired:
            stmt = stmt.where(Capability.status != "retired")
        return [_row_to_card(r) for r in db.scalars(stmt).all()]


def discover(
    kind: str | None = None,
    tag: str | None = None,
    input_fields: list[str] | None = None,
    active_only: bool = True,
    latest_only: bool = False,
) -> list[CapabilityCard]:
    """按 kind / tag / input_schema 字段发现能力卡（§7.4「tags + input_schema 发现」）。

    input_fields：要求卡声明能接受的输入字段（卡 input_schema.fields 的子集请求）；
    latest_only：每个 name 只保留版本序最高的 active 卡（§7.4 latest 版本策略）。
    """
    cards = list_cards(include_retired=not active_only)
    if active_only:
        cards = [c for c in cards if c.status == "active"]
    if kind:
        cards = [c for c in cards if c.kind == kind]
    if tag:
        cards = [c for c in cards if tag in c.tags]
    if input_fields:
        def _covers(c: CapabilityCard) -> bool:
            declared = set(c.input_schema.get("fields") or [])
            return set(input_fields) <= declared
        cards = [c for c in cards if _covers(c)]
    if latest_only:
        by_name: dict[str, CapabilityCard] = {}
        for c in cards:
            cur = by_name.get(c.name)
            newer = cur is None or version_key(c.version) > version_key(cur.version)
            if newer:
                by_name[c.name] = c
        cards = list(by_name.values())
    return cards


def register(card: CapabilityCard) -> CapabilityCard:
    """注册 / 更新（upsert）：新版本插入；已有 (name, version) 更新可变字段。

    这也是回滚通道——POST 一张已有卡改 traffic_weight，等价于更新一行绑定记录。
    """
    _maybe_seed_if_empty()
    with SessionLocal() as db:
        row = db.get(Capability, (card.name, card.version))
        if row is None:
            row = Capability(name=card.name, version=card.version)
            db.add(row)
        row.kind = card.kind
        row.input_schema = json.dumps(card.input_schema, ensure_ascii=False)
        row.output_schema = json.dumps(card.output_schema, ensure_ascii=False)
        row.endpoint = card.endpoint
        row.healthcheck = json.dumps(card.healthcheck, ensure_ascii=False)
        row.cost_estimate = card.cost_estimate
        row.tags = ",".join(card.tags)
        row.status = card.status
        row.traffic_weight = card.traffic_weight
        db.commit()
    return card


def set_status(name: str, version: str, status: Status) -> CapabilityCard | None:
    if status not in ("active", "unhealthy", "retired"):
        raise ValueError(f"unknown status: {status}")
    with SessionLocal() as db:
        row = db.get(Capability, (name, version))
        if row is None:
            return None
        row.status = status
        db.commit()
        return _row_to_card(row)


def set_weight(name: str, version: str, traffic_weight: int) -> CapabilityCard | None:
    """回滚 = 改这一行（§7.4）。"""
    with SessionLocal() as db:
        row = db.get(Capability, (name, version))
        if row is None:
            return None
        row.traffic_weight = traffic_weight
        db.commit()
        return _row_to_card(row)


def deregister(name: str, version: str) -> CapabilityCard | None:
    """注销 = status→retired 归档（不物理删除，卡片列表仍可追溯）。"""
    return set_status(name, version, "retired")


def _stable_hash(trace_id: str) -> int:
    # Python 内置 hash() 对 str 有进程级盐——换 hashlib 才能跨进程可复现
    return int(hashlib.sha256(trace_id.encode()).hexdigest()[:16], 16)


def _weighted_pick(cards: list[CapabilityCard], trace_id: str) -> CapabilityCard | None:
    """确定性加权选卡：stable_hash(trace_id) % Σweights 落入的累计区间。"""
    total = sum(c.traffic_weight for c in cards)
    if total <= 0:
        return None
    h = _stable_hash(trace_id) % total
    acc = 0
    for c in cards:
        acc += c.traffic_weight
        if h < acc:
            return c
    return cards[-1]  # 理论不可达（防御性兜底）


def _active_cards(where: tuple) -> list[CapabilityCard]:
    _maybe_seed_if_empty()
    with SessionLocal() as db:
        rows = db.scalars(
            select(Capability)
            .where(*where, Capability.status == "active")
            .order_by(Capability.name, Capability.version)
        ).all()
        return [_row_to_card(r) for r in rows]


def route(kind: str, trace_id: str) -> CapabilityCard | None:
    """在 kind 的 active 卡中按 traffic_weight 加权选一张（确定性，见模块 docstring）。"""
    return _weighted_pick(_active_cards((Capability.kind == kind,)), trace_id)


def route_required(kind: str, trace_id: str) -> CapabilityCard:
    card = route(kind, trace_id)
    if card is None:
        raise CapabilityUnavailableError(
            f"no routable '{kind}' capability: 未注册 / 心跳摘除 / 权重全 0"
        )
    return card


def route_name(name: str, trace_id: str) -> CapabilityCard | None:
    """在指定 name 的 active 卡中加权选一张。

    供主链路闸门使用（solver/matrix/verifier 各自 name）——按 kind 桶选会误把
    同 kind 的无关卡当闸门（如再注册一张 tool 卡即掩盖 matrix 摘除）。
    """
    return _weighted_pick(_active_cards((Capability.name == name,)), trace_id)


def route_name_required(name: str, trace_id: str) -> CapabilityCard:
    card = route_name(name, trace_id)
    if card is None:
        raise CapabilityUnavailableError(
            f"no routable '{name}' capability: 未注册 / 心跳摘除 / 权重全 0"
        )
    return card


def policy_view(name: str) -> dict:
    """§7.4 版本策略的当前事实视图（pinned | canary | off + latest 版本号）。

    pinned = 权重全量绑定单一版本（回滚即重新 pin）；canary = 多版本分权重灰度；
    off = 无承接版本。latest 一栏始终给出该 name 版本序最高的 active 卡，
    新卡以权重 0 注册、由人放量——保守默认，不自动切 latest。
    """
    cards = _active_cards((Capability.name == name,))
    total = sum(c.traffic_weight for c in cards)
    if not cards or total <= 0:
        policy = "off"
    elif sum(1 for c in cards if c.traffic_weight > 0) == 1:
        policy = "pinned"
    else:
        policy = "canary"
    latest = max((c.version for c in cards), key=version_key, default=None)
    return {
        "name": name,
        "policy": policy,
        "latest": latest,
        "versions": [
            {"version": c.version, "weight": c.traffic_weight,
             "status": c.status} for c in cards
        ],
    }
