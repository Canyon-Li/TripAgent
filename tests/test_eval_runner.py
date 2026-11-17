"""3.2/3.3：用例 schema 加载、graders 评分、baseline 回归规则、runner 进程内冒烟。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval import graders, report
from app.eval.cases import ChatCase, PlanCase, load_eval_cases
from app.eval.graders import (
    M_BUDGET,
    M_FINAL,
    M_HARD,
    M_P95,
    M_RECALL,
    M_TIMEWIN,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- 用例集 ----------

def test_loader_validates_counts_and_dupes(tmp_path: Path):
    good = [
        {"_meta": {"version": "t.1", "count": 1, "public": 1, "hidden": 0}},
        {"case_id": "a", "request": {"days": 1, "budget": 100, "start_date": "2026-11-18"},
         "asserts": {"status": "feasible"}},
    ]
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in good), encoding="utf-8")
    meta, cases = load_eval_cases(p)
    assert meta.count == 1 and isinstance(cases[0], PlanCase)

    bad = [dict(good[0], **{"_meta": {"version": "t.1", "count": 2, "public": 2, "hidden": 0}}),
           good[1]]
    p2 = tmp_path / "bad.jsonl"
    p2.write_text("\n".join(json.dumps(x) for x in bad), encoding="utf-8")
    with pytest.raises(ValueError, match="计数不符"):
        load_eval_cases(p2)


def test_real_dataset_meta_consistent():
    meta, cases = load_eval_cases(REPO_ROOT / "data" / "eval_cases.jsonl")
    assert meta.count == 40 and meta.hidden == 8 and meta.public == 32
    assert sum(1 for c in cases if isinstance(c, ChatCase)) == 8


# ---------- graders ----------

def _plan_case() -> PlanCase:
    return PlanCase.model_validate({
        "case_id": "g1", "request": {"days": 1, "budget": 1000, "start_date": "2026-11-18",
                                     "must_visit": ["tiananmen_square"]},
        "asserts": {"status": "feasible"},
    })


def _feasible_body() -> dict:
    return {
        "trace_id": "t", "status": "feasible",
        "days": [{"stops": [{"poi": "tiananmen_square"}]}],
        "verification": {"violations": [], "recomputed_budget": 30.0},
        "infeasible_reasons": [], "cost": {"latency_ms": 12},
    }


def test_grade_plan_feasible_and_infeasible():
    r = graders.grade_plan_case(_plan_case(), _feasible_body(), None)
    assert r.passed and r.metrics[M_HARD] == (True, 1.0)
    assert r.metrics[M_FINAL][0]

    body = _feasible_body()
    body["verification"]["violations"] = [{"type": "window_closed"}]
    r2 = graders.grade_plan_case(_plan_case(), body, None)
    assert not r2.passed
    assert r2.metrics[M_TIMEWIN] == (False, 1.0)

    r3 = graders.grade_plan_case(_plan_case(), None, "HTTP 500")
    assert not r3.passed and "HTTP 500" in r3.failures


def test_grade_chat_memory_recall_and_tools():
    case = ChatCase.model_validate({
        "case_id": "c1", "session_id": "s", "start_date": "2026-11-18",
        "turns": [{"message": "预算8000玩2天"}],
        "asserts": {
            "final_status": "feasible", "turn_types": ["plan"],
            "memory_recall": [
                {"field": "budget", "value": 8000, "source_prefix": "memory_views#", "turn": 1},
                {"field": "must_visit", "value": ["a", "b"], "source_prefix": "memory_views#"},
            ],
        },
    })
    done = {
        "type": "plan", "trace_id": "tr-1",
        "plan": {
            "status": "feasible",
            "days": [{"stops": [{"poi": "a"}, {"poi": "b"}]}],
            "verification": {"violations": [], "recomputed_budget": 100.0},
            "applied_constraints": {"budget": 8000, "constraints": [
                {"field": "budget", "value": 8000, "source": "memory_views#7", "turn": 1},
                {"field": "must_visit", "value": ["a", "b"], "source": "memory_views#8",
                 "turn": 1},
            ]},
        },
        "llm": {"token_in": 10, "token_out": 5},
    }
    runs = {"tr-1": {"spans": [
        {"name": "resolver.resolve", "status": "ok", "meta": {"guardrail_violations": []}},
        {"name": "solver.solve", "status": "ok", "meta": {}},
        {"name": "verifier.check", "status": "ok", "meta": {}},
    ]}}
    r = graders.grade_chat_case(case, [done], None, runs)
    assert r.passed, r.failures
    assert r.metrics[M_RECALL] == (True, 1.0)
    assert r.metrics["tool_success_rate.function_calling"] == (True, 1.0)
    assert r.metrics["tool_success_rate.capability"] == (True, 1.0)

    # 记忆召回值错 → recall 失败（§12 memory_recall 逐字段）
    bad_done = json.loads(json.dumps(done))
    bad_done["plan"]["applied_constraints"]["constraints"][0]["value"] = 9999
    r2 = graders.grade_chat_case(case, [bad_done], None, runs)
    assert not r2.passed and r2.metrics[M_RECALL] == (False, 0.0)


def test_aggregate_rates_and_p95():
    results = []
    for ok in (True, False, True):
        c = graders.CaseResult(f"c{ok}", False, "plan", passed=ok)
        c.set(M_HARD, ok, 1.0 if ok else 0.0)
        c.set(M_FINAL, ok, 1.0 if ok else 0.0)
        c.set(M_TIMEWIN, ok, 0.0 if ok else 2.0)
        c.set(M_BUDGET, True, 1.0)
        c.latency_ms = 100 if ok else 500
        results.append(c)
    agg = graders.aggregate(results)
    assert agg[M_HARD] == pytest.approx(2 / 3)
    assert agg[M_TIMEWIN] == 2.0
    assert agg[M_P95] == 500.0  # 3 条的 P95 = 最大值


# ---------- 回归阻断规则（§12） ----------

def test_compare_baseline_rules(tmp_path: Path):
    base = {M_HARD: 0.95, M_TIMEWIN: 0.0, M_BUDGET: 1.0, M_RECALL: 0.9,
            graders.M_FINAL: 0.95}
    p = tmp_path / "baseline.json"
    report.write_baseline(base, "sha0", "t.1", p)

    # 指标持平或改善 → 无回归
    assert report.compare_baseline(
        {M_HARD: 0.95, M_TIMEWIN: 0.0, M_BUDGET: 1.0, M_RECALL: 0.92,
         graders.M_FINAL: 0.96}, p) == []

    # 硬约束类退化 → 回归
    msgs = report.compare_baseline(
        {M_HARD: 0.90, M_TIMEWIN: 0.0, M_BUDGET: 1.0, M_RECALL: 0.9,
         graders.M_FINAL: 0.95}, p)
    assert any("hard_constraint" in m for m in msgs)

    # 时间窗冲突变多 → 回归
    msgs = report.compare_baseline(
        {M_HARD: 0.95, M_TIMEWIN: 2.0, M_BUDGET: 1.0, M_RECALL: 0.9,
         graders.M_FINAL: 0.95}, p)
    assert any("time_window" in m for m in msgs)

    # final_pass_rate 降 1pp（容差内）不阻断，降 3pp 阻断
    assert report.compare_baseline(
        {M_HARD: 0.95, M_TIMEWIN: 0.0, M_BUDGET: 1.0, M_RECALL: 0.9,
         graders.M_FINAL: 0.94}, p) == []
    assert any("final_pass_rate" in m for m in report.compare_baseline(
        {M_HARD: 0.95, M_TIMEWIN: 0.0, M_BUDGET: 1.0, M_RECALL: 0.9,
         graders.M_FINAL: 0.92}, p))

    # 基线缺失 → 明确报错
    assert report.compare_baseline({}, tmp_path / "none.json")


# ---------- baseline A（离线 stub） ----------

def test_baseline_a_grades_llm_itinerary_and_skips_chat():
    """A 列仅评 plan 用例：stub LLM 输出可解析行程 → 校验器复核；chat 用例不入列。"""
    from app.eval import baseline_a
    from app.llm.adapter import LLMResult

    class StubLLM:
        def __init__(self, texts):  # noqa: ANN001
            self.texts = list(texts)

        def chat(self, *, messages, tools=None):  # noqa: ANN001
            return LLMResult(text=self.texts.pop(0), model="stub")

    plan_ok = PlanCase.model_validate({
        "case_id": "a_ok", "request": {
            "days": 1, "budget": 2000, "start_date": "2026-11-18",
            "must_visit": ["tiananmen_square"]},
        "asserts": {"status": "feasible"},
    })
    plan_bad = PlanCase.model_validate({
        "case_id": "a_bad", "request": {
            "days": 1, "budget": 2000, "start_date": "2026-11-18",
            "must_visit": ["tiananmen_square"]},
        "asserts": {"status": "feasible"},
    })
    chat = ChatCase.model_validate({
        "case_id": "a_chat", "session_id": "s", "start_date": "2026-11-18",
        "turns": [{"message": "x"}],
        "asserts": {"final_status": "feasible"},
    })
    good_text = ('{"days":[{"date_index":0,"stops":['
                 '{"poi":"tiananmen_square","arrive":"09:00","leave":"10:00"}]}]}')
    llm = StubLLM([good_text, "这不是 JSON"])
    results = baseline_a.run([plan_ok, plan_bad, chat], llm)
    assert [r.case_id for r in results] == ["a_ok", "a_bad"]  # chat 不入 A 列
    assert results[0].kind == "plan"
    # 解析失败 → 记失败原因（诚实计数，不掩盖）
    assert any("baseline_A" in f for f in results[1].failures)


# ---------- runner 进程内冒烟 ----------

def test_run_eval_smoke_fake(tmp_path: Path, monkeypatch):
    """2 条 plan 用例（可行+不可行）走完整 runner：报告 + 聚合 + eval_results 入库。"""
    cases = [
        {"_meta": {"version": "smoke.1", "count": 2, "public": 2, "hidden": 0}},
        {"case_id": "smoke_ok", "request": {
            "days": 1, "budget": 2000, "start_date": "2026-11-18",
            "must_visit": ["tiananmen_square"]},
         "asserts": {"status": "feasible"}},
        {"case_id": "smoke_bad", "request": {
            "days": 1, "budget": 1, "start_date": "2026-11-18",
            "must_visit": ["forbidden_city"]},
         "asserts": {"status": "infeasible",
                     "reason_contains": ["must_visit_not_covered: forbidden_city"]}},
    ]
    p = tmp_path / "smoke.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in cases),
                 encoding="utf-8")
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path / "reports")

    from app.eval import runner
    result = runner.run_eval(p, baselines=("current",), llm_mode="fake")

    assert len(result["columns"]["current"]) == 2
    assert all(c["passed"] for c in result["columns"]["current"])
    assert result["aggregates"]["current"][M_FINAL] == 1.0
    report_md = Path(result["report_path"])
    assert report_md.exists() and "final_pass_rate" in report_md.read_text(encoding="utf-8")

    from app.db import evals as evals_db
    trend = evals_db.list_metric_trend(M_FINAL)
    assert any(t["passed"] == t["total"] for t in trend)
