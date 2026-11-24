"""2.10 一致性验收（§13 阶段 2 口径）：

①六轮约束演进，第 6 轮 applied_constraints 正确回显各约束的来源与首提轮次
  （memory_recall@6：早期约束不因轮次推移滑点）；
②中途改预算 → supersedes 归档（Ledger 两条都在，当前生效新值）；
③Ledger 全量回放 + rebuild 前后一致；
④用户隔离（在 test_api_memory.py 已覆盖，此处回归断言第 6 轮边界）。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.chat import get_llm_optional
from app.memory import ledger
from tests.fakes import FakeLLM, tool_call_result


def _turn(client: TestClient, session: str, user: str, message: str, fields: dict,
          start_date: str = "2026-11-18") -> dict:
    """一轮对话：FakeLLM resolver 只报本轮显式字段（模型正确行为的脚本化）。

    start_date 每轮都带（不入记忆，缺省时每轮独立锚 date.today()）且固定周三：
    故宫等周一闭馆，否则断言随星期几漂移（audit 2026-11-23 P1；与 eval runner 同做法）。
    """
    from app.main import app

    fake = FakeLLM([
        tool_call_result(("submit_plan_request", json.dumps(fields))),
    ])
    app.dependency_overrides[get_llm_optional] = lambda: fake
    try:
        r = client.post("/api/chat", json={
            "session_id": session, "user_id": user, "message": message,
            "start_date": start_date,
        })
    finally:
        app.dependency_overrides.pop(get_llm_optional, None)
    assert r.status_code == 200
    done_line = [ln for ln in r.text.split("\n") if ln.startswith("data: ")][-1]
    done = json.loads(done_line[len("data: "):])
    assert done["type"] == "plan", done
    return done


def test_six_turn_evolution_recall(client):
    s, u = "six-1", "u-six"
    # 轮 1：定预算与天数、必去故宫
    d1 = _turn(client, s, u, "预算1万玩2天，必去故宫",
               {"budget": 10000, "days": 2, "must_visit": ["forbidden_city"]})
    assert d1["turn"] == 1 and d1["plan"]["applied_constraints"]["source"] == "request"
    # 轮 2：偏好
    _turn(client, s, u, "喜欢历史", {"preferences": ["history"]})
    # 轮 3：改预算（应 supersede 归档 10000）
    _turn(client, s, u, "预算改成8000", {"budget": 8000})
    # 轮 4：加必去点
    _turn(client, s, u, "再加上颐和园", {"must_visit": ["summer_palace"]})
    # 轮 5：每日时长
    _turn(client, s, u, "每天在外不超过8小时", {"daily_minutes_cap": 480})
    # 轮 6：只改天数——其余全部来自记忆
    d6 = _turn(client, s, u, "改成玩3天", {"days": 3})

    assert d6["turn"] == 6
    ac = d6["plan"]["applied_constraints"]
    by = {c["field"]: c for c in ac["constraints"]}
    # memory_recall@6：第 6 轮早期约束仍正确注入（budget 为第 3 轮改后的当前有效值）
    assert ac["budget"] == 8000
    assert ac["source"].startswith("memory_views#") and ac["turn"] == 3
    assert by["budget"]["turn"] == 3 and by["budget"]["source"].startswith("memory_views#")
    assert by["preferences"]["turn"] == 2
    assert by["daily_minutes_cap"]["turn"] == 5
    assert by["days"]["turn"] == 6 and by["days"]["source"] == "request"
    # 列表并集保持早期成员（轮 4 显式 + 轮 1 记忆）；来源保持记忆侧不被本轮覆写
    # （turn = 当前并集值确立的轮次 4；成员首提轮次 1 由 Ledger 历史可回放）
    assert set(by["must_visit"]["value"]) == {"summer_palace", "forbidden_city"}
    assert by["must_visit"]["turn"] == 4
    assert by["must_visit"]["source"].startswith("memory_views#")
    # 全量重规划可行且覆盖
    assert d6["plan"]["status"] == "feasible" and len(d6["plan"]["days"]) == 3
    pois = {stop["poi"] for day in d6["plan"]["days"] for stop in day["stops"]}
    assert {"summer_palace", "forbidden_city"} <= pois


def test_supersede_archived_and_replayable(client):
    s, u = "six-2", "u-sup"
    _turn(client, s, u, "预算7000玩1天", {"budget": 7000, "days": 1})
    _turn(client, s, u, "预算改成6500", {"budget": 6500})
    # Ledger：两条 budget assertion，新 supersedes 旧；旧值不丢（可回放）
    budget_events = [
        e for e in ledger.list_events(scope=f"user:{u}", event_type="assertion")
        if e["content"]["field"] == "budget"
    ]
    assert [e["content"]["value"] for e in budget_events] == [7000.0, 6500.0]
    assert budget_events[1]["supersedes_id"] == budget_events[0]["id"]
    # 活跃视图只有新值
    from app.memory import service
    got = service.read_memory(session_id=s, user_id=u)
    assert next(c for c in got if c.field == "budget").value == 6500.0


def test_rebuild_consistent_after_six_turns(client):
    s, u = "six-3", "u-rb6"
    _turn(client, s, u, "预算5000玩1天必去故宫",
          {"budget": 5000, "days": 1, "must_visit": ["forbidden_city"]})
    _turn(client, s, u, "改成2天", {"days": 2})
    _turn(client, s, u, "加上天坛", {"must_visit": ["temple_of_heaven"]})
    r = client.post("/api/memory/rebuild").json()
    assert r["consistent"] is True
    assert f"session:{s}" in r["scopes"] and f"user:{u}" in r["scopes"]
    # 重建后 read 仍完整（Ledger 是唯一权威事实来源）
    from app.memory import service
    got = {c.field: (c.value, c.turn) for c in service.read_memory(session_id=s, user_id=u)}
    assert got["budget"] == (5000.0, 1)
    assert got["days"] == (2, 2)
    assert set(got["must_visit"][0]) == {"forbidden_city", "temple_of_heaven"}
