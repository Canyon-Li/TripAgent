"""3.x（§7.3 回归闭环末环）：线上坏例一键转评测用例。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.eval import badcase


def _seed_plan_run(client: TestClient) -> str:
    r = client.post("/api/plan", json={
        "city": "beijing", "days": 1, "hotel": "wangfujing", "budget": 2000,
        "daily_minutes_cap": 480, "must_visit": ["forbidden_city"],
        "session_id": "s-badcase", "start_date": "2026-11-18",
    })
    assert r.status_code == 200
    return r.json()["trace_id"]


def test_fetch_build_and_append_roundtrip(client: TestClient, tmp_path: Path):
    trace_id = _seed_plan_run(client)

    run = badcase.fetch_run(trace_id)
    assert run is not None and run["response"]["status"] == "feasible"

    case = badcase.build_case(run)
    assert case["case_id"] == f"badcase_{trace_id.removeprefix('tr-')}"
    assert case["request"]["must_visit"] == ["forbidden_city"]
    assert case["asserts"]["status"] == "feasible"
    assert case["asserts"]["time_window_violations"] == 0
    assert case["_from_trace"] == trace_id  # 草稿带来源提示，append 时剔除

    # 追加进评测集：_meta 计数同步 + 整文件可重新加载
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join([
        json.dumps({"_meta": {"version": "t.1", "count": 1, "public": 1, "hidden": 0}}),
        json.dumps({"case_id": "a", "request": {"days": 1, "budget": 100,
                                                "start_date": "2026-11-18"},
                    "asserts": {"status": "feasible"}}),
    ]) + "\n", encoding="utf-8")
    badcase.append_case(p, case)
    from app.eval.cases import load_eval_cases
    meta, cases = load_eval_cases(p)
    assert meta.count == 2 and meta.public == 2
    assert cases[-1].case_id == case["case_id"]


def test_chat_run_builds_case_from_echo(client: TestClient, monkeypatch):
    """chat run：用末轮 applied_constraints 合并终值还原结构化请求。"""
    from app.api.chat import get_llm_optional
    from app.main import app
    from tests.fakes import FakeLLM, tool_call_result

    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(
            {"budget": 8000, "days": 2, "must_visit": ["forbidden_city"]}))),
        tool_call_result(text="ok"),
    ])
    app.dependency_overrides[get_llm_optional] = lambda: fake
    try:
        # user_id 必须独占命名空间：user scope 跨会话注入（B9），与其他测试共用
        # user id 会把本测试写入的 budget 泄漏进 test_memory_read 等用例
        r = client.post("/api/chat", json={
            "session_id": "s-badcase-chat", "user_id": "u-badcase-only",
            "message": "预算8000玩2天必去故宫", "start_date": "2026-11-18"})
        trace_id = None
        for block in r.text.strip().split("\n\n"):
            if block.startswith("event: done"):
                trace_id = json.loads(block.split("\n")[1][6:])["trace_id"]
    finally:
        app.dependency_overrides.pop(get_llm_optional, None)

    run = badcase.fetch_run(trace_id)
    case = badcase.build_case(run)
    assert case["request"]["budget"] == 8000
    assert case["request"]["days"] == 2
    assert case["request"]["must_visit"] == ["forbidden_city"]
    assert case["request"]["start_date"] == "2026-11-18"
    assert case["asserts"]["status"] == "feasible"
