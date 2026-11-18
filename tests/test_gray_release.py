"""灰度评测断言（ADR-007）：确定性评测集直接断言 v2 的灰度安全性。

两件事：
1. v2 全量接管（权重 0/100）时公开 plan 种子用例过同一套 graders——
   硬约束不因目标权重变化而破坏（灰度的安全前提）；
2. 至少一例 v1/v2 的取舍结果确有差异（objective / visited）——
   证明 v2 不是换皮 v1，灰度演示有实际观感。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.eval.cases import PlanCase
from app.eval.graders import grade_plan_case
from app.registry import service

_CASES_PATH = Path(__file__).resolve().parents[1] / "data" / "eval_cases.jsonl"


def _plan_cases() -> list[PlanCase]:
    cases = []
    for line in _CASES_PATH.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "_meta" in row or row.get("kind") == "chat":
            continue
        if not row.get("hidden"):
            cases.append(PlanCase.model_validate({**row, "kind": "plan"}))
    return cases


@pytest.fixture(autouse=True)
def _restore_weights(default_registry_state):
    yield


def test_v2_full_takeover_passes_public_plan_cases(client: TestClient):
    """v2 权重 100：公开 plan 用例全量跑，grader 断言全过（ADR-007 灰度安全前提）。"""
    service.set_weight("solver", "v1", 0)
    service.set_weight("solver", "v2", 100)

    failures = []
    for case in _plan_cases():
        r = client.post("/api/plan", json=case.request.model_dump())
        assert r.status_code == 200, f"{case.case_id}: HTTP {r.status_code}"
        graded = grade_plan_case(case, r.json(), None)
        if not graded.passed:
            failures.append(f"{case.case_id}: {graded.failures}")
    assert not failures, "v2 全量接管下有用例未过: " + "; ".join(failures)


def test_v2_actually_differs_from_v1(client: TestClient):
    """同一请求 v1/v2 各跑一遍：至少一例 objective 或 visited 集合不同（确有取舍差异）。"""
    probe = next(c for c in _plan_cases() if c.request.days >= 3)

    def _solve_with(version: str) -> dict:
        service.set_weight("solver", "v1", 100 if version == "v1" else 0)
        service.set_weight("solver", "v2", 0 if version == "v1" else 100)
        r = client.post("/api/plan", json=probe.request.model_dump())
        assert r.status_code == 200
        body = r.json()
        spans = client.get(f"/api/runs/{body['trace_id']}").json()["spans"]
        meta = next(s for s in spans if s["name"] == "solver.solve")["meta"]
        assert meta["solver_version"] == version  # 路由确实指到了指定版本
        return {"objective": body["solver"]["objective"],
                "visited": {s["poi"] for d in body["days"] for s in d["stops"]}}

    v1, v2 = _solve_with("v1"), _solve_with("v2")
    assert v1["visited"], "探针用例应产出非空行程"
    assert v1 != v2, "v1/v2 目标权重不同但结果完全一致——灰度载体失去意义"


def test_default_weights_keep_v1_for_ci_determinism(client: TestClient):
    """默认权重（v1=100, v2=0）路由恒 v1：CI 评测基线与阶段 3 冻结口径零漂移。"""
    for i in range(10):
        assert service.route("solver", f"tr-ci-{i}").version == "v1"
