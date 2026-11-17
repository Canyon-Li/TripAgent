"""断言式评分（ADR-004：确定性脚本判定，不用 LLM-as-judge 参与 final_pass_rate）。

输入是「用例 + 运行产物」（plan 响应 / chat 各轮 done 事件 + spans），输出 CaseResult：
- 每条用例的适用断言全过与否（final_pass_rate 的分子）；
- §12 指标的每用例取值（passed, value），由 report 聚合成列。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.eval.cases import ChatCase, PlanCase

# §12 指标名（eval_results.metric 列的取值域）
M_HARD = "hard_constraint_pass_rate"
M_TIMEWIN = "time_window_violations"
M_BUDGET = "budget_accuracy"
M_RECALL = "memory_recall@6"
M_TOOL_FC = "tool_success_rate.function_calling"
M_TOOL_CAP = "tool_success_rate.capability"
M_P95 = "p95_latency_ms"
M_COST = "cost_per_plan"
M_FINAL = "final_pass_rate"

# 回归阻断口径（§12 验收规则）：硬约束类任一退化即回归；final_pass_rate 降 >2pp CI 失败。
# p95 / cost 为记录型指标，不阻断。
HARD_METRICS = (M_HARD, M_TIMEWIN, M_BUDGET, M_RECALL)
FINAL_DROP_TOLERANCE = 0.02


@dataclass
class CaseResult:
    case_id: str
    hidden: bool
    kind: str  # plan | chat
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, tuple[bool, float]] = field(default_factory=dict)
    latency_ms: float = 0.0
    token_in: int = 0
    token_out: int = 0
    trace_ids: list[str] = field(default_factory=list)

    def fail(self, why: str) -> None:
        self.passed = False
        self.failures.append(why)

    def set(self, metric: str, passed: bool, value: float) -> None:
        self.metrics[metric] = (passed, float(value))


def _num_eq(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)


def grade_plan_case(case: PlanCase, body: dict | None, http_error: str | None = None) -> CaseResult:
    """plan 单轮用例评分（唯一断言口径来源，check_plans 也走这里）。

    断言分两类（§12）：硬约束类（status/必去覆盖/预算一致且不超支/时间窗/不可行证据）
    进 hard_constraint_pass_rate 与 budget_accuracy；行为类（days_count）只进 final。
    """
    r = CaseResult(case.case_id, case.hidden, "plan")
    a = case.asserts
    if http_error or body is None:
        r.fail(http_error or "无响应")
        for m in (M_HARD, M_BUDGET, M_FINAL):
            r.set(m, False, 0.0)
        r.set(M_TIMEWIN, False, 1)
        return r

    r.latency_ms = body.get("cost", {}).get("latency_ms") or 0
    r.trace_ids = [body.get("trace_id", "")]
    tw_violations = len(body.get("verification", {}).get("violations") or [])
    hard_ok = True
    over_budget = False
    echo_consistent = True

    if body.get("status") != a.status:
        hard_ok = False
        r.fail(f"status={body.get('status')}，期望 {a.status}")
    if body.get("status") == "feasible":
        stops = {s["poi"] for d in body.get("days", []) for s in d.get("stops", [])}
        if a.must_visit_covered:
            missing = set(case.request.must_visit) - stops
            if missing:
                hard_ok = False
                r.fail(f"必去点未覆盖: {sorted(missing)}")
        spent = body.get("verification", {}).get("recomputed_budget", math.inf)
        if a.budget_within and spent > case.request.budget + 1e-6:
            hard_ok = False
            over_budget = True
            r.fail(f"预算超支: 重算 {spent} > {case.request.budget}")
        # §12 budget_accuracy「一致」：回显的生效预算不得篡改请求预算
        echo_budget = body.get("applied_constraints", {}).get("budget")
        if echo_budget is not None and not _num_eq(echo_budget, case.request.budget):
            hard_ok = False
            echo_consistent = False
            r.fail(f"预算回显不一致: {echo_budget} != 请求 {case.request.budget}")
        if a.days_count is not None and len(body.get("days", [])) != a.days_count:
            r.fail(f"天数 {len(body.get('days', []))} != {a.days_count}")  # 行为类，不进硬约束
        if tw_violations != a.time_window_violations:
            hard_ok = False
            r.fail(f"校验器检出 {tw_violations} 处冲突（期望 {a.time_window_violations}）")
    else:
        if body.get("days"):
            hard_ok = False
            r.fail("infeasible 却返回了行程")
        if not body.get("infeasible_reasons"):
            hard_ok = False
            r.fail("infeasible 缺少原因")
        for needle in a.reason_contains:
            if not any(needle in reason for reason in body.get("infeasible_reasons", [])):
                hard_ok = False
                r.fail(f"原因缺少子串 {needle!r}")

    budget_ok = not over_budget and echo_consistent
    r.set(M_HARD, hard_ok, 1.0 if hard_ok else 0.0)
    r.set(M_TIMEWIN, tw_violations == a.time_window_violations, float(tw_violations))
    r.set(M_BUDGET, budget_ok, 1.0 if budget_ok else 0.0)
    r.set(M_P95, True, r.latency_ms)  # 记录型（§12 不阻断）
    r.set(M_COST, True, float(r.token_in + r.token_out))
    r.set(M_FINAL, r.passed, 1.0 if r.passed else 0.0)
    return r


def grade_chat_case(
    case: ChatCase,
    turn_dones: list[dict],
    http_error: str | None,
    runs_by_trace: dict[str, dict],
) -> CaseResult:
    """chat 多轮用例评分：轮次类型、末轮终态、memory_recall 回显、可行方案的硬约束。"""
    r = CaseResult(case.case_id, case.hidden, "chat")
    a = case.asserts
    if http_error:
        r.fail(http_error)
        for m in (M_HARD, M_BUDGET, M_RECALL, M_FINAL):
            r.set(m, False, 0.0)
        r.set(M_TIMEWIN, False, 1)
        return r

    r.trace_ids = [d.get("trace_id", "") for d in turn_dones if d]
    last = turn_dones[-1] if turn_dones else {}
    # 每轮耗时/token 累计
    for d in turn_dones:
        plan = d.get("plan") or {}
        r.latency_ms += plan.get("cost", {}).get("latency_ms") or d.get("latency_ms") or 0
        llm = d.get("llm") or {}
        r.token_in += llm.get("token_in") or 0
        r.token_out += llm.get("token_out") or 0

    # 轮次类型（行为类断言：不进硬约束，只进 final）
    types = [d.get("type", "?") for d in turn_dones]
    if a.turn_types is not None and types != list(a.turn_types):
        r.fail(f"轮次类型 {types} != 期望 {list(a.turn_types)}")

    hard_ok = True  # 硬约束类（§12）：末轮可执行性 + 必去/预算/时间窗/不可行证据
    over_budget = False
    final_plan = last.get("plan") or {}
    final_status = last.get("type") if last.get("type") == "clarification" \
        else final_plan.get("status", "?")
    if final_status != a.final_status:
        hard_ok = False
        r.fail(f"末轮终态 {final_status} != 期望 {a.final_status}")

    # memory_recall（§12 memory_recall@6 的逐用例形态）：末轮 plan 的约束回显
    echo = {c["field"]: c for c in final_plan.get("applied_constraints", {}).get(
        "constraints", [])}
    recall_ok = True
    for want in a.memory_recall:
        got = echo.get(want.field)
        if got is None:
            recall_ok = False
            r.fail(f"memory_recall: 末轮缺字段 {want.field}")
            continue
        if isinstance(want.value, list):
            ok = set(map(str, got.get("value") or [])) == set(map(str, want.value))
        else:
            ok = _num_eq(got.get("value"), want.value)
        if not ok:
            recall_ok = False
            r.fail(f"memory_recall[{want.field}]: 值 {got.get('value')} != 期望 {want.value}")
        if want.source_prefix and not str(got.get("source", "")).startswith(want.source_prefix):
            recall_ok = False
            r.fail(f"memory_recall[{want.field}]: 来源 {got.get('source')} 不含前缀 "
                   f"{want.source_prefix}")
        if want.turn is not None and got.get("turn") != want.turn:
            recall_ok = False
            r.fail(f"memory_recall[{want.field}]: 轮次 {got.get('turn')} != {want.turn}")
    r.set(M_RECALL, recall_ok, 1.0 if recall_ok else 0.0)

    # 可行末轮的硬约束（与 plan 用例同口径；budget 一致性子集：chat 的生效预算来自
    # 记忆/显式合并，其与期望值的一致性已由 memory_recall 断言覆盖，此处只查不超支）
    tw_violations = len(final_plan.get("verification", {}).get("violations") or [])
    if a.final_status == "feasible" and final_plan.get("status") == "feasible":
        stops = {s["poi"] for d in final_plan.get("days", []) for s in d.get("stops", [])}
        want_must = next(
            (w.value for w in a.memory_recall if w.field == "must_visit"), [])
        if a.must_visit_covered and want_must:
            missing = set(want_must) - stops
            if missing:
                hard_ok = False
                r.fail(f"必去点未覆盖: {sorted(missing)}")
        spent = final_plan.get("verification", {}).get("recomputed_budget", math.inf)
        budget = final_plan.get("applied_constraints", {}).get("budget", math.inf)
        if a.budget_within and spent > float(budget) + 1e-6:
            hard_ok = False
            over_budget = True
            r.fail(f"预算超支: 重算 {spent} > {budget}")
        if tw_violations != a.time_window_violations:
            hard_ok = False
            r.fail(f"校验器检出 {tw_violations} 处冲突")
    elif a.final_status == "infeasible" and final_plan.get("status") == "infeasible":
        for needle in a.reason_contains:
            if not any(needle in reason
                       for reason in final_plan.get("infeasible_reasons", [])):
                hard_ok = False
                r.fail(f"原因缺少子串 {needle!r}")

    # tool_success_rate（两类，§7.6）：从 spans 统计
    fc_ok = fc_total = 0
    cap_ok = cap_total = 0
    for trace_id in r.trace_ids:
        for s in (runs_by_trace.get(trace_id) or {}).get("spans", []):
            meta = s.get("meta") or {}
            if s["name"] == "resolver.resolve":
                fc_total += 1
                if not meta.get("guardrail_violations"):
                    fc_ok += 1
            elif s["name"] in ("solver.solve", "verifier.check"):
                cap_total += 1
                if s.get("status") == "ok":
                    cap_ok += 1
    if fc_total:
        r.set(M_TOOL_FC, fc_ok == fc_total, fc_ok / fc_total)
    if cap_total:
        r.set(M_TOOL_CAP, cap_ok == cap_total, cap_ok / cap_total)

    r.set(M_HARD, hard_ok, 1.0 if hard_ok else 0.0)
    r.set(M_TIMEWIN, tw_violations == a.time_window_violations, float(tw_violations))
    r.set(M_BUDGET, not over_budget, 0.0 if over_budget else 1.0)
    r.set(M_P95, True, r.latency_ms)  # 记录型：chat 为各轮耗时和（真 P95 见报告聚合）
    r.set(M_COST, True, float(r.token_in + r.token_out))
    r.set(M_FINAL, r.passed, 1.0 if r.passed else 0.0)
    return r


def aggregate(results: list[CaseResult]) -> dict[str, float]:
    """§12 聚合：率类 = 通过数/适用数；冲突数 = 求和；延迟 P95；成本均值。"""
    agg: dict[str, float] = {}

    def _rate(metric: str, *, applicable_kinds: set[str] | None = None) -> float:
        vals = [r.metrics[metric] for r in results
                if metric in r.metrics
                and (applicable_kinds is None or r.kind in applicable_kinds)]
        return sum(1 for p, _ in vals if p) / len(vals) if vals else float("nan")

    agg[M_HARD] = _rate(M_HARD)
    agg[M_BUDGET] = _rate(M_BUDGET)
    agg[M_RECALL] = _rate(M_RECALL, applicable_kinds={"chat"})
    agg[M_FINAL] = _rate(M_FINAL)
    tw = [r.metrics[M_TIMEWIN][1] for r in results if M_TIMEWIN in r.metrics]
    agg[M_TIMEWIN] = float(sum(tw)) if tw else float("nan")
    fc = [r.metrics[M_TOOL_FC][1] for r in results if M_TOOL_FC in r.metrics]
    if fc:
        agg[M_TOOL_FC] = sum(fc) / len(fc)
    cap = [r.metrics[M_TOOL_CAP][1] for r in results if M_TOOL_CAP in r.metrics]
    if cap:
        agg[M_TOOL_CAP] = sum(cap) / len(cap)
    lat = sorted(r.latency_ms for r in results)
    if lat:
        idx = max(0, math.ceil(0.95 * len(lat)) - 1)
        agg[M_P95] = float(lat[idx])
    toks = [(r.token_in, r.token_out) for r in results]
    if toks:
        agg[M_COST] = sum(i + o for i, o in toks) / len(toks)
    return agg
