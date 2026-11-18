"""Registry 服务层（§7.4 / ADR-007）：种子、upsert、注销、发现、确定性加权路由、回滚。"""

from __future__ import annotations

import pytest

from app.registry import service
from app.registry.cards import CapabilityCard
from app.registry.seed import DEFAULT_CARDS, SOLVER_VERSIONS


def _purge(name: str, version: str) -> None:
    """测试专用的物理删除（服务层只归档不删除——清理测试残留用）。"""
    from app.db.base import SessionLocal
    from app.db.models import Capability

    with SessionLocal() as db:
        row = db.get(Capability, (name, version))
        if row:
            db.delete(row)
            db.commit()


@pytest.fixture(autouse=True)
def _seeded(default_registry_state):
    service.ensure_seed()
    yield


def _solver_versions(weights: dict[str, int], traces: list[str]) -> set[str]:
    for v, w in weights.items():
        service.set_weight("solver", v, w)
    return {service.route("solver", t).version for t in traces}


class TestSeed:
    def test_seed_is_idempotent(self):
        before = {c.ref for c in service.list_cards()}
        service.ensure_seed()
        service.ensure_seed()
        assert {c.ref for c in service.list_cards()} == before

    def test_seed_does_not_overwrite_modified_weight(self):
        service.set_weight("solver", "v2", 55)
        service.ensure_seed()  # 幂等补插绝不覆写已改行（回滚状态得以保留）
        assert service.get_card("solver", "v2").traffic_weight == 55
        service.set_weight("solver", "v2", 0)

    def test_default_cards_cover_discovery_chain(self):
        """§7.1 发现链三件套 + LLM 适配器，默认 5 张卡、v2 权重 0（基线零漂移）。"""
        cards = {c.ref for c in service.list_cards()}
        assert cards == {"solver@v1", "solver@v2", "matrix@static", "verifier@v1",
                         "llm_adapter@deepseek"}
        assert service.get_card("solver", "v2").traffic_weight == 0
        assert set(SOLVER_VERSIONS) == {"v1", "v2"}
        assert len(DEFAULT_CARDS) == 5


class TestRegisterAndLifecycle:
    def test_register_inserts_new_card(self):
        card = CapabilityCard(
            name="solver", version="v3-lab", kind="solver",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            endpoint="inprocess://test", healthcheck={"type": "self_test_solve"},
            tags=["planning"], traffic_weight=0,
        )
        assert service.register(card).ref == "solver@v3-lab"
        got = service.get_card("solver", "v3-lab")
        assert got.tags == ["planning"] and got.status == "active"
        service.deregister("solver", "v3-lab")
        _purge("solver", "v3-lab")  # 物理清场：retired 残留会混进心跳/列表类断言

    def test_register_upserts_weight_and_status(self):
        card = service.get_card("solver", "v2").model_copy(update={"traffic_weight": 40})
        service.register(card)
        got = service.get_card("solver", "v2")
        assert got.traffic_weight == 40 and got.version == "v2"
        service.set_weight("solver", "v2", 0)

    def test_deregister_archives_not_deletes(self):
        service.deregister("matrix", "static")
        cards = service.list_cards(include_retired=False)
        assert "matrix@static" not in {c.ref for c in cards}
        assert "matrix@static" in {c.ref for c in service.list_cards(include_retired=True)}
        assert service.get_card("matrix", "static").status == "retired"
        service.set_status("matrix", "static", "active")

    def test_discover_by_kind_and_tag(self):
        assert {c.ref for c in service.discover(kind="solver")} == {"solver@v1", "solver@v2"}
        assert "matrix@static" in {c.ref for c in service.discover(kind="tool", tag="matrix")}
        assert all(c.status == "active" for c in service.discover(kind="verifier"))


class TestDeterministicRouting:
    def test_same_trace_same_version(self, default_registry_state):
        picks = {service.route("solver", f"tr-fixed-{i}").version for i in range(20)}
        assert picks == {"v1"}  # v2 权重 0：全部落 v1

    def test_all_v2_routes_to_v2(self, default_registry_state):
        assert _solver_versions({"v1": 0, "v2": 100}, [f"tr-x-{i}" for i in range(20)]) == {"v2"}

    def test_mixed_weights_cover_both_deterministically(self, default_registry_state):
        traces = [f"tr-mix-{i}" for i in range(200)]
        picks = _solver_versions({"v1": 80, "v2": 20}, traces)
        assert picks == {"v1", "v2"}  # 200 条必然两版都见
        # 确定性：重放同一批 trace 结果逐一一致（ADR-007「评测集可断言」的基础）
        again = [service.route("solver", t).version for t in traces]
        first = [service.route("solver", t).version for t in traces]
        assert again == first

    def test_route_returns_none_when_all_removed_or_zero(self, default_registry_state):
        service.set_status("solver", "v1", "unhealthy")
        service.set_status("solver", "v2", "unhealthy")
        try:
            assert service.route("solver", "tr-any") is None
            with pytest.raises(service.CapabilityUnavailableError):
                service.route_required("solver", "tr-any")
        finally:
            service.set_status("solver", "v1", "active")
            service.set_status("solver", "v2", "active")

    def test_unhealthy_excluded_but_sibling_takes_over(self, default_registry_state):
        """摘除 v1 → 全部流量落 v2（单版可用即服务不中断，§7.4 平台价值）。"""
        service.set_weight("solver", "v2", 100)  # v2 有权重，摘除 v1 才有接管者
        service.set_status("solver", "v1", "unhealthy")
        try:
            picks = {service.route("solver", f"tr-fb-{i}").version for i in range(20)}
            assert picks == {"v2"}
        finally:
            service.set_status("solver", "v1", "active")


class TestRollbackWithoutRestart:
    def test_set_weight_is_the_rollback_lever(self, default_registry_state):
        """回滚 = 改一行权重：v2 全量 → 回 v1 全量，路由立即生效（不重启）。"""
        _solver_versions({"v1": 0, "v2": 100}, ["tr-rb-0"])
        assert service.route("solver", "tr-rb-0").version == "v2"
        service.set_weight("solver", "v2", 0)  # 回滚：一行记录
        service.set_weight("solver", "v1", 100)
        assert service.route("solver", "tr-rb-0").version == "v1"


class TestVersionPolicy:
    """§7.4 pinned | canary | latest 版本策略（policy_view 事实视图 + latest 发现）。"""

    def test_default_state_is_pinned_to_v1(self, default_registry_state):
        view = service.policy_view("solver")
        assert view["policy"] == "pinned"  # 权重全量绑定单一版本
        assert {v["version"] for v in view["versions"]} == {"v1", "v2"}
        assert view["latest"] == "v2"  # 版本序最高的 active 卡（不自动承接）

    def test_mixed_weights_read_as_canary(self, default_registry_state):
        service.set_weight("solver", "v1", 80)
        service.set_weight("solver", "v2", 20)
        assert service.policy_view("solver")["policy"] == "canary"

    def test_all_removed_reads_as_off(self, default_registry_state):
        service.set_status("solver", "v1", "unhealthy")
        service.set_status("solver", "v2", "unhealthy")
        assert service.policy_view("solver")["policy"] == "off"

    def test_discover_latest_only_returns_highest_per_name(self, default_registry_state):
        cards = service.discover(latest_only=True)
        by_name = {c.name: c.version for c in cards}
        assert by_name["solver"] == "v2" and by_name["matrix"] == "static"
        assert len([c for c in cards if c.name == "solver"]) == 1


class TestDiscoverBySchema:
    """§7.4「按 tags + input_schema 发现」。"""

    def test_input_fields_subset_match(self, default_registry_state):
        hits = service.discover(kind="solver", input_fields=["days", "budget"])
        assert {c.ref for c in hits} == {"solver@v1", "solver@v2"}

    def test_input_fields_not_covered_misses(self, default_registry_state):
        assert service.discover(kind="solver", input_fields=["days", "weather"]) == []


class TestRouteNameGate:
    """闸门按 name 而非 kind：同 kind 无关卡不能冒名顶替（Spec 审查 (c)4）。"""

    def test_tool_imposter_cannot_mask_matrix_removal(self, default_registry_state):
        imposter = CapabilityCard(
            name="geo_backup", version="v1", kind="tool",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            endpoint="inprocess://test", healthcheck={"type": "repository_load"},
            tags=["matrix"], traffic_weight=100,
        )
        service.register(imposter)
        try:
            service.set_status("matrix", "static", "unhealthy")
            assert service.route_name("matrix", "tr-gate") is None  # name 闸门不受影响
            assert service.route("tool", "tr-gate") is not None     # kind 桶确实被顶替（对照）
        finally:
            service.set_status("matrix", "static", "active")
            _purge("geo_backup", "v1")

    def test_route_name_routes_solver_versions(self, default_registry_state):
        assert service.route_name("solver", "tr-sn-1").ref == "solver@v1"


class TestStatusValidation:
    def test_set_status_rejects_unknown_value(self, default_registry_state):
        import pytest

        with pytest.raises(ValueError, match="unknown status"):
            service.set_status("solver", "v1", "draining")


class TestDynamicCardHeartbeat:
    """心跳按卡内 healthcheck.type 分发：动态注册的卡同样被心跳覆盖（Spec 审查 (c)3）。"""

    def test_dynamic_self_test_card_heartbeats_and_fails(self, default_registry_state):
        from app.registry import health as registry_health

        card = CapabilityCard(
            name="solver", version="v9-lab", kind="solver",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            endpoint="inprocess://test", healthcheck={"type": "self_test_solve"},
            tags=[], traffic_weight=0,
        )
        service.register(card)
        try:
            results = registry_health.run_heartbeat()
            # SOLVER_VERSIONS 未收录 v9-lab：有卡无参数绑定 = 配置不健康，照样摘除
            assert results["solver@v9-lab"]["ok"] is False
            assert "无参数绑定" in results["solver@v9-lab"]["detail"]
            assert service.get_card("solver", "v9-lab").status == "unhealthy"
        finally:
            _purge("solver", "v9-lab")

    def test_unknown_healthcheck_type_is_unknown_not_removed(self, default_registry_state):
        from app.registry import health as registry_health

        card = CapabilityCard(
            name="Notifier", version="v1", kind="tool",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            endpoint="https://example.invalid", healthcheck={"type": "http_get"},
            tags=["notify"], traffic_weight=0,
        )
        service.register(card)
        try:
            results = registry_health.run_heartbeat()
            entry = results["Notifier@v1"]
            assert entry["ok"] is None and "unknown healthcheck type" in entry["detail"]
            assert service.get_card("Notifier", "v1").status == "active"  # 不误摘
        finally:
            _purge("Notifier", "v1")
