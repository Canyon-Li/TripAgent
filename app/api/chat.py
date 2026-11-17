"""POST /api/chat（§10 SSE 流式）——阶段 2 多轮约束演进对话主链路（§7.1 / B12）。

单轮流程（每轮全量重规划，B12）：
    observation 落账 → Memory.read（权威注入）
    → Resolver（白名单 function calling，B14；recall_constraint 由运行时检索
      memory_views 并回灌模型，B15；LLM 缺失/失败 → 规则模板降级）
    → merge（显式优先、记忆兜底；recall 召回值与权威通道同源去重合并，B15）
    → apply_assertions 写回 Ledger + refresh Views
    → run_plan_pipeline（solve + verify，与 /api/plan 同一条确定性路径）
    → Responder（LLM 解释 / 降级模板）
SSE 事件：start / stage / clarification / delta / done / error。
runs/spans 全记录：resolver.resolve、memory.read、memory.write、
solver.solve、verifier.check、responder.render（trace_id 贯穿）；
异常 → error 事件 + run 落 failed；客户端断开 → interrupted（§7.1）。
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.config import get_settings
from app.db import runs as runs_db
from app.db.repository import DataRepository, get_repository
from app.llm.adapter import LLMAdapter, UsageMeter, get_llm, llm_available
from app.llm.tools import PREFERENCE_KEYWORDS
from app.memory import ledger, policy, service, views
from app.memory.service import ConstraintOrigin
from app.runtime.pipeline import Tick, run_plan_pipeline, span_bounds, tick
from app.runtime.resolver import RecallLookup, resolve
from app.runtime.responder import compose_reply, split_deltas
from app.schemas import AppliedConstraintsOut, ChatRequest, ConstraintEcho, PlanRequest

router = APIRouter(prefix="/api")

HISTORY_WINDOW = 12  # 注入 Resolver 的最近轮数（observation 原话）


def _get_llm_optional() -> LLMAdapter | None:
    """无 key 时返回 None——主链路直接走规则模板降级（§7.1），不 500。"""
    return get_llm() if llm_available() else None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _span(trace_id: str, name: str, t0: Tick, status: str = "ok", **meta: Any) -> None:
    runs_db.add_span(trace_id, name, *span_bounds(t0), status, meta=meta or None)


def _build_history(session_id: str) -> list[dict[str, str]]:
    """对话历史 = 该 session 最近 N 条用户原话（Ledger observation，§7.2）。"""
    obs = ledger.list_events(session_id=session_id, event_type="observation")
    return [
        {"role": "user", "content": f"[第{o['content']['turn']}轮] {o['content']['text']}"}
        for o in obs[-HISTORY_WINDOW:-1]  # 本轮原话在 resolver 里单独作为最后一条 user
    ]


def _recall_lookup(session_id: str, user_id: str) -> RecallLookup:
    """B15：recall_constraint 的执行体——同源 memory_views，按 scope 链检索。"""
    chain = policy.scope_chain(session_id, user_id)

    def lookup(key: str) -> dict[str, Any] | None:
        for scope in chain:
            v = views.current_view(scope)
            if v is None:
                continue
            constraints = v["value"]["constraints"]
            if key in constraints:
                prov = v["value"]["provenance"].get(key, {})
                return {
                    "value": constraints[key],
                    "source": f"memory_views#{v['id']}",
                    "turn": prov.get("turn") or 0,
                    "scope": scope,
                }
        return None

    return lookup


def _merge_recall_into_memory(
    memory: list[ConstraintOrigin],
    recalled: dict[str, dict[str, Any]],
) -> list[ConstraintOrigin]:
    """B15 两路合并去重：recall 召回值与 Memory.read 同源，正常被权威通道覆盖；
    权威链未含该字段时以 recall 结果补位（链路完备性，测试可注入构造覆盖）。"""
    have = {c.field for c in memory}
    extra = [
        ConstraintOrigin(
            field=key, value=hit["value"], source=hit["source"],
            scope=hit.get("scope", "recall"), turn=hit.get("turn", 0),
        )
        for key, hit in recalled.items() if key not in have
    ]
    return memory + extra


def _normalize_fields(
    fields: dict[str, Any], poi_index: tuple[tuple[str, str], ...]
) -> tuple[dict[str, Any], list[str]]:
    """数据级护栏：LLM 常输出中文名/中文类目而非 id——统一映射，未知条目剔除记违规。"""
    violations: list[str] = []
    must = fields.get("must_visit")
    if must:
        id_by_name = dict(poi_index)
        known = {pid for pid, _ in poi_index}
        normalized, seen = [], set()
        for item in must:
            pid = item if item in known else id_by_name.get(item)
            if pid is None:
                violations.append(f"unknown_poi_dropped:{item}")
                continue
            if pid not in seen:
                seen.add(pid)
                normalized.append(pid)
        if normalized:
            fields["must_visit"] = normalized
        else:
            fields.pop("must_visit", None)
    prefs = fields.get("preferences")
    if prefs:
        # 中文类目词 → id（如“历史”→history；规则模板解析的 PREFERENCE_KEYWORDS 反查）
        word_to_cat = {w: cat for cat, words in PREFERENCE_KEYWORDS.items() for w in words}
        valid_cats = set(PREFERENCE_KEYWORDS)
        normalized, seen = [], set()
        for item in prefs:
            cat = item if item in valid_cats else word_to_cat.get(item)
            if cat is None:
                violations.append(f"unknown_preference_dropped:{item}")
                continue
            if cat not in seen:
                seen.add(cat)
                normalized.append(cat)
        if normalized:
            fields["preferences"] = normalized
        else:
            fields.pop("preferences", None)
    return fields, violations


@router.post("/chat")
def chat(
    req: ChatRequest,
    llm: Annotated[LLMAdapter | None, Depends(_get_llm_optional)],
) -> StreamingResponse:
    return StreamingResponse(
        _chat_stream(req, llm),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _chat_stream(req: ChatRequest, llm: LLMAdapter | None) -> Iterator[str]:
    t0 = time.perf_counter()
    settings = get_settings()
    repo = get_repository()
    meter = UsageMeter()

    trace_id = f"tr-{uuid.uuid4().hex[:12]}"
    runs_db.create_run(
        trace_id=trace_id,
        session_id=req.session_id,
        request_json=req.model_dump_json(),
        git_sha=settings.git_sha,
    )
    yield _sse("start", {"trace_id": trace_id, "session_id": req.session_id,
                         "user_id": req.user_id})

    def _finish(status: str = "succeeded") -> int:
        latency = int((time.perf_counter() - t0) * 1000)
        runs_db.finish_run(
            trace_id, status, latency,
            token_in=meter.token_in, token_out=meter.token_out,
            model=meter.model or None,
        )
        return latency

    try:
        yield from _run_turn(req, llm, repo=repo, trace_id=trace_id, meter=meter, finish=_finish)
    except GeneratorExit:
        # 客户端断开：run 标 interrupted（§7.1 优雅关闭语义），不 yield（协议禁止）
        _finish("interrupted")
        raise
    except Exception as exc:  # noqa: BLE001 —— SSE error 统一出口，run 落 failed
        _finish("failed")
        yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}",
                             "trace_id": trace_id})


def _run_turn(
    req: ChatRequest,
    llm: LLMAdapter | None,
    *,
    repo: DataRepository,
    trace_id: str,
    meter: UsageMeter,
    finish: Callable[[str], int],
) -> Iterator[str]:
    """单轮主体（§7.1 主链路）；成功路径内部自行 finish("succeeded")。"""
    poi_index = tuple((p.id, p.name) for p in repo.pois)
    poi_catalog = "\n".join(f"{pid} = {name}" for pid, name in poi_index)

    # ---- 1. 本轮落账（observation）----
    turn = policy.next_turn(req.session_id)
    policy.record_observation(
        session_id=req.session_id, user_id=req.user_id, text=req.message, turn=turn
    )

    # ---- 2. Memory.read（权威注入 B15；MEMORY_INJECTION=off 关闭 = 基线 B，ADR-006）----
    m0 = tick()
    if get_settings().memory_injection:
        memory = service.read_memory(session_id=req.session_id, user_id=req.user_id)
    else:
        memory = []
    active = service.origins_to_fields(memory)
    _span(trace_id, "memory.read", m0, fields=len(memory),
          sources={c.field: c.source for c in memory})
    yield _sse("stage", {"name": "memory", "status": "done", "fields": len(memory)})

    # ---- 3. Resolver（白名单 function calling 回路 + B15 recall 检索 / 降级）----
    r0 = tick()
    outcome = resolve(
        llm,
        message=req.message,
        active_constraints=active,
        history=_build_history(req.session_id),
        meter=meter,
        poi_index=poi_index,
        poi_catalog=poi_catalog,
        recall_lookup=_recall_lookup(req.session_id, req.user_id)
        if get_settings().memory_injection else (lambda _key: None),
    )
    fields, norm_violations = _normalize_fields(outcome.fields, poi_index)
    outcome.guardrail_violations += norm_violations
    _span(trace_id, "resolver.resolve", r0,
          kind=outcome.kind, fields=fields, degraded=outcome.degraded,
          recalled=dict(outcome.recalled), recalled_misses=outcome.recalled_misses,
          guardrail_violations=outcome.guardrail_violations, turn=turn)
    yield _sse("stage", {
        "name": "resolver", "status": "done", "kind": outcome.kind,
        "degraded": outcome.degraded,
        "guardrail_violations": outcome.guardrail_violations,
    })

    def _clarify(field: str, reason: str) -> Iterator[str]:
        latency = finish()
        yield _sse("clarification", {"field": field, "reason": reason, "turn": turn})
        yield _sse("done", {
            "type": "clarification", "trace_id": trace_id, "turn": turn,
            "field": field, "reason": reason, "reply": reason,
            "llm": meter.as_dict(), "latency_ms": latency,
        })

    def _replay_fallback(note: str) -> Iterator[str]:
        """§7.1「规则模板 + 最近一次可行方案」降级：LLM 已失败且本轮无法产出新方案时，
        回退本 session 最近一次可行行程（runs.response_json），明确标注降级与来源。"""
        hit = runs_db.get_last_feasible_run(req.session_id, exclude_trace_id=trace_id)
        if hit is None:
            return None
        fb_trace, fb_plan = hit["trace_id"], dict(hit["response"])
        reply = (
            f"（LLM 暂不可用，已降级）{note}。"
            f"先沿用您最近一次可行方案（产生于 trace {fb_trace}），稍后可再调整。"
        )
        fb_plan["trace_id"] = trace_id  # 回放挂在当前 trace 下，便于瀑布图定位
        yield _sse("stage", {"name": "fallback", "status": "done", "trace_id": fb_trace})
        p0 = tick()
        _span(trace_id, "responder.render", p0, degraded=True,
              fallback="last_feasible", fallback_trace_id=fb_trace)
        for chunk in split_deltas(reply):
            yield _sse("delta", {"text": chunk})
        runs_db.save_response(trace_id, fb_plan)
        finish()
        yield _sse("done", {
            "type": "plan", "trace_id": trace_id, "turn": turn,
            "reply": reply, "plan": fb_plan,
            "fallback": {"type": "last_feasible", "trace_id": fb_trace},
            "llm": meter.as_dict(),
        })
        return fb_trace

    if outcome.kind != "plan":
        if outcome.degraded:
            # 规则模板也没解析出约束：优先回退最近一次可行方案（§7.1），无历史再澄清
            field, reason = outcome.clarification or ("message", "请说明行程约束")
            replayed = yield from _replay_fallback(f"本轮未能解析出行约束（{reason}）")
            if replayed:
                return
        field, reason = outcome.clarification or ("message", "请说明行程约束")
        yield from _clarify(field, reason)
        return

    # ---- 4. merge（显式优先、记忆兜底；recall 两路同源去重，B15）→ 硬字段检查 ----
    merged = service.merge_constraints(
        fields, _merge_recall_into_memory(memory, outcome.recalled), turn=turn
    )
    final = service.origins_to_fields(merged)
    if not final.get("budget"):
        yield from _clarify(
            "budget", "还不知道预算——请告诉我这趟最多花多少钱（记忆里也没有历史预算可用）"
        )
        return

    # ---- 5. 写回 Ledger（合并终值；未变化字段由 Policy 跳过）+ refresh Views ----
    w0 = tick()
    report = policy.apply_assertions(
        session_id=req.session_id, user_id=req.user_id,
        fields={k: v for k, v in final.items() if k in policy.FIELD_SCOPES},
        turn=turn,
    )
    for scope in {policy.session_scope(req.session_id), policy.user_scope(req.user_id)}:
        views.refresh_view(scope)
    _span(trace_id, "memory.write", w0, written=report.written,
          superseded=report.superseded, skipped=report.skipped_unchanged)
    yield _sse("stage", {"name": "memory.write", "status": "done",
                         "written": len(report.written), "superseded": len(report.superseded)})

    # ---- 6. 全量重规划（solve + verify，与 /api/plan 同路径）----
    try:
        plan_req = PlanRequest(
            session_id=req.session_id,
            start_date=dt.date.fromisoformat(req.start_date) if req.start_date
            else dt.date.today(),
            days=int(final.get("days", 3)),
            budget=float(final["budget"]),
            daily_minutes_cap=int(final.get("daily_minutes_cap", 480)),
            must_visit=list(final.get("must_visit", [])),
            preferences=list(final.get("preferences", [])),
            hotel=str(final.get("hotel", "wangfujing")),
        )
    except ValidationError as exc:
        yield from _clarify("constraints", f"约束取值不合法：{exc.errors()[0]['msg']}")
        return
    yield _sse("stage", {"name": "solver", "status": "start"})
    plan_resp = run_plan_pipeline(repo=repo, req=plan_req, trace_id=trace_id)
    plan_resp.applied_constraints = AppliedConstraintsOut(
        budget=plan_req.budget,
        source=next(c.source for c in merged if c.field == "budget"),
        turn=next(c.turn for c in merged if c.field == "budget"),
        constraints=[ConstraintEcho(**c.to_public()) for c in merged],
    )
    yield _sse("stage", {"name": "verifier", "status": "done",
                         "plan_status": plan_resp.status})

    if plan_resp.status == "infeasible" and outcome.degraded:
        # §7.1 降级链尾：LLM 已失败 + 重规划不可行 → 回退最近一次可行方案，无历史才走不可行解释
        reasons = "；".join(plan_resp.infeasible_reasons[:3])
        replayed = yield from _replay_fallback(f"按当前约束重新规划不可行（{reasons}）")
        if replayed:
            return

    # ---- 7. Responder（LLM 解释 / 降级模板）+ SSE 增量 ----
    p0 = tick()
    plan_dict = plan_resp.model_dump(mode="json")
    echo = [c.to_public() for c in merged]
    reply = compose_reply(
        llm, user_message=req.message, plan_response_dict=plan_dict,
        constraints_echo=echo, meter=meter,
    )
    _span(trace_id, "responder.render", p0, degraded=meter.degraded)
    for chunk in split_deltas(reply):
        yield _sse("delta", {"text": chunk})

    plan_resp.cost.token_in = meter.token_in
    plan_resp.cost.token_out = meter.token_out
    latency = finish()
    plan_resp.cost.latency_ms = latency
    plan_dump = plan_resp.model_dump(mode="json")
    runs_db.save_response(trace_id, plan_dump)
    yield _sse("done", {
        "type": "plan", "trace_id": trace_id, "turn": turn,
        "reply": reply,
        "plan": plan_dump,
        "memory": {
            "read": [c.to_public() for c in memory],
            "written": report.written,
            "superseded": report.superseded,
            "recalled": outcome.recalled,
            "recalled_misses": outcome.recalled_misses,
            "guardrail_violations": outcome.guardrail_violations,
        },
        "llm": meter.as_dict(),
    })
