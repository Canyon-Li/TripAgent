"""Capability Registry（§7.4，阶段 4）：注册 / 发现 / 加权路由 / 心跳健康检查。

给**运行时路由器**用的能力卡注册表，不是给模型看的 tool list（§7.6 的分界）。
灰度权重与版本绑定存 SQLite、路由时读取——回滚只需更新一行记录（回滚不重启）。
"""

from app.registry.cards import CapabilityCard
from app.registry.service import (
    CapabilityUnavailableError,
    deregister,
    discover,
    get_card,
    list_cards,
    register,
    route,
    set_status,
    set_weight,
)

__all__ = [
    "CapabilityCard",
    "CapabilityUnavailableError",
    "discover",
    "deregister",
    "get_card",
    "list_cards",
    "register",
    "route",
    "set_status",
    "set_weight",
]
