"""基线 A（ADR-006）：纯 LLM 直接生成行程，复现 TripGenie 式失效。

同一 PlanRequest 一次性交给 LLM（不给工具、不给管线），要求输出行程 JSON；
再用**独立校验器**（ADR-001，LLM 行程当作声明复核）+ 与 current 完全相同的
graders 断言评分——时间线/闭馆/预算/必去覆盖的违反数即基线 A 的真实水平。
仅适用 plan 用例（chat 多轮用例不适用，报告标 N/A）。
"""

from __future__ import annotations

import json
import re

from app.db.repository import get_repository
from app.eval.cases import PlanCase
from app.eval.graders import CaseResult, grade_plan_case
from app.verifier.verify import DayClaim, StopClaim, VerifyRequest, verify_itinerary

_SYSTEM = (
    "你是行程规划助手。根据用户的行程要求和景点清单，直接排一份逐日行程，"
    "只输出一个 JSON 对象，不要输出其他文字。格式："
    '{"days":[{"date_index":0,"stops":[{"poi":"<景点id>","arrive":"09:00","leave":"12:00"}]}]}'
    "。要求：天数与用户要求一致；必去景点全部包含；到达/离开时间在景点开放窗口内、"
    "停留不少于建议时长；门票总花费不超过预算；避开景点闭馆的星期；"
    "每天在外总时长（含路程）不超过每日上限。"
)


def _catalog(repo) -> str:  # noqa: ANN001
    lines = []
    for p in repo.pois:
        closed = ""
        if p.closed_weekdays:
            names = "周" + "、周".join("一二三四五六日"[w] for w in p.closed_weekdays)
            closed = f"，{names}闭馆"
        lines.append(
            f"{p.id} = {p.name}（类目 {p.category}，门票 {p.ticket} 元，"
            f"建议 {p.duration_min} 分钟，开放 {p.open}-{p.close}{closed}）"
        )
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _hhmm_to_min(s: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s).strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h < 24 and 0 <= mi < 60:
        return h * 60 + mi
    return None


def _to_claims(output: dict) -> tuple[list[DayClaim], list[str]]:
    """LLM JSON → 校验器声明；解析不了的站/日记为失败原因并跳过（诚实计数）。"""
    claims: list[DayClaim] = []
    errors: list[str] = []
    for day in output.get("days") or []:
        idx = day.get("date_index")
        stops: list[StopClaim] = []
        for s in day.get("stops") or []:
            arrive = _hhmm_to_min(s.get("arrive", ""))
            leave = _hhmm_to_min(s.get("leave", ""))
            if arrive is None or leave is None or not s.get("poi"):
                errors.append(f"stop 字段非法: {s}")
                continue
            stops.append(StopClaim(str(s["poi"]), arrive, leave))
        if not isinstance(idx, int) or not stops:
            errors.append(f"day 字段非法: {day}")
            continue
        claims.append(DayClaim(idx, depart_min=stops[0].arrive_min, stops=tuple(stops)))
    return claims, errors


def run(cases: list[PlanCase], llm) -> list[CaseResult]:  # noqa: ANN001
    repo = get_repository()
    catalog = _catalog(repo)
    results: list[CaseResult] = []

    for case in cases:
        req = case.request
        user_msg = (
            f"行程要求：{req.days} 天，预算 {req.budget} 元（含门票与市内交通），"
            f"每天在外不超过 {req.daily_minutes_cap} 分钟，"
            f"从 {req.start_date} 开始，"
            f"必去：{req.must_visit or '无'}，偏好：{req.preferences or '无'}。\n"
            f"景点清单（id = 名称（属性））：\n{catalog}"
        )
        try:
            resp = llm.chat(messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ])
            text = resp.text or ""
        except Exception as exc:  # noqa: BLE001 —— LLM 失败本身就是基线 A 的结果
            results.append(_failed(case, f"baseline_A llm error: {exc}"))
            continue

        output = _extract_json(text)
        if output is None:
            results.append(_failed(case, "baseline_A 输出非 JSON"))
            continue
        claims, parse_errors = _to_claims(output)
        if not claims:
            results.append(_failed(
                case, f"baseline_A 无可解析行程: {parse_errors[:3]}"))
            continue

        verification = verify_itinerary(repo, VerifyRequest(
            days=req.days, budget=req.budget,
            daily_minutes_cap=req.daily_minutes_cap,
            must_visit=frozenset(req.must_visit), start_date=req.start_date,
        ), claims)
        violations = list(verification.violations)
        if parse_errors:
            violations = violations + parse_errors  # 解析失败按违反计数（诚实口径）

        pseudo_body = {
            "trace_id": "",
            "status": "feasible",  # LLM 声称排出了行程（能否站住由断言判定）
            "days": [
                {"date_index": d.date_index,
                 "stops": [{"poi": s.poi_id} for s in d.stops]}
                for d in claims
            ],
            "verification": {
                "violations": [
                    {"type": v.type, "detail": v.detail, "day_index": v.day_index,
                     "poi": v.poi}
                    for v in violations
                ],
                "recomputed_budget": verification.recomputed_budget,
            },
            "infeasible_reasons": [],
            "cost": {"latency_ms": 0, "token_in": 0, "token_out": 0},
        }
        # 期望 infeasible 的用例：LLM 硬凑出行程即违背「不硬凑」——由下方补记失败
        r = grade_plan_case(case, pseudo_body, None)
        if case.asserts.status == "infeasible" and not r.failures:
            r.fail("baseline A 硬凑了不可行请求的行程（未拒绝）")
            r.passed = False
            r.metrics["final_pass_rate"] = (False, 0.0)
            r.metrics["hard_constraint_pass_rate"] = (False, 0.0)
        results.append(r)
    return results


def _failed(case: PlanCase, why: str) -> CaseResult:
    r = grade_plan_case(case, None, why)
    return r
