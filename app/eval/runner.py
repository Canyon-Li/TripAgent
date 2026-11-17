"""eval runner（§7.3 / §11.3）：`python -m app.eval.runner [--baseline current|A|B|all]`。

- current：现行管线（plan 用例打 /api/plan，chat 用例逐轮打 /api/chat SSE）；
- B（ADR-006）：完整管线关闭记忆注入（MEMORY_INJECTION=off，含 recall 通道）；
- A（ADR-006）：纯 LLM 直接生成行程，仅 plan 用例（chat 用例不适用，报告标 N/A），
  过同一套 graders 断言（独立校验器复核 LLM 行程，复现 TripGenie 式失效）。

LLM 模式：--real 强制真 key（无 key 报错）；--fake 强制用例内嵌脚本（CI 确定性）；
默认 auto——有 key 用真 LLM，无 key 回退脚本。CLI 入口用临时库（不污染 data/），
run_eval() 也可被 /api/eval/run 在进程内调用（沿用当前 DATABASE_URL）。

报告：reports/eval-<sha>.md 三列 + eval_results 入库（current 列）+ baseline.json（CI 对比）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------- SSE / LLM 替身 ----------

def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) < 2 or not lines[0].startswith("event: "):
            continue
        events.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return events


class ScriptedLLM:
    """按用例内嵌脚本驱动的 LLM 替身（CI 无 key 的确定性模式；协议同 FakeLLM）。"""

    def __init__(self, script: list) -> None:  # noqa: ANN001 —— app.eval.cases.FakeStep
        from app.llm.adapter import LLMResult, ToolCall

        self._result_cls, self._tool_cls = LLMResult, ToolCall
        self.queue: list = []
        for step in script:
            if step.error == "retryable":
                from app.llm.adapter import LLMRetryableError
                self.queue.append(LLMRetryableError("scripted retryable"))
            elif step.error == "fatal":
                from app.llm.adapter import LLMError
                self.queue.append(LLMError("scripted fatal"))
            elif step.tool_calls:
                self.queue.append(LLMResult(
                    tool_calls=tuple(
                        self._tool_cls(id=f"call_{i}", name=name, arguments_json=args)
                        for i, (name, args) in enumerate(step.tool_calls)
                    ),
                    token_in=100, token_out=20, model="scripted",
                    finish_reason="tool_calls",
                ))
            else:
                self.queue.append(LLMResult(text=step.text, model="scripted"))

    def chat(self, *, messages: list, tools: list | None = None):  # noqa: ANN001
        if not self.queue:
            return self._result_cls(text="(script exhausted)", model="scripted")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------- 内存注入开关（基线 B） ----------

def _with_memory_injection(on: bool):  # noqa: ANN202 —— 上下文管理器
    from contextlib import contextmanager

    from app.config import get_settings

    @contextmanager
    def _ctx():
        original = os.environ.get("MEMORY_INJECTION")
        os.environ["MEMORY_INJECTION"] = "on" if on else "off"
        get_settings.cache_clear()
        try:
            yield
        finally:
            if original is None:
                os.environ.pop("MEMORY_INJECTION", None)
            else:
                os.environ["MEMORY_INJECTION"] = original
            get_settings.cache_clear()

    return _ctx()


# ---------- 用例执行 ----------

def _run_plan_case(client, case) -> tuple[dict | None, str | None]:  # noqa: ANN001
    r = client.post("/api/plan", json=case.request.model_dump())
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json(), None


def _run_chat_case(client, app, case, *, use_scripts: bool):  # noqa: ANN001
    """逐轮 POST /api/chat，收集每轮 done 事件；脚本模式下按轮安装 ScriptedLLM。"""
    from app.api.chat import _get_llm_optional

    dones: list[dict] = []
    http_error: str | None = None
    for turn in case.turns:
        payload = {
            "session_id": turn.session_id or case.session_id,
            "user_id": case.user_id,
            "message": turn.message,
            "start_date": case.start_date,
        }
        if use_scripts:
            scripted = ScriptedLLM(turn.fake_llm)
            app.dependency_overrides[_get_llm_optional] = lambda s=scripted: s
        try:
            r = client.post("/api/chat", json=payload)
            if r.status_code != 200:
                http_error = f"HTTP {r.status_code}: {r.text[:200]}"
                break
            events = parse_sse(r.text)
            err = next((d for e, d in events if e == "error"), None)
            done = next((d for e, d in reversed(events) if e == "done"), None)
            if err is not None:
                http_error = f"SSE error: {err.get('detail', '')[:200]}"
                break
            if done is None:
                http_error = "SSE 流缺 done 事件"
                break
            dones.append(done)
        finally:
            if use_scripts:
                app.dependency_overrides.pop(_get_llm_optional, None)
    return dones, http_error


def _spans_for_traces(client, trace_ids: list[str]) -> dict[str, dict]:  # noqa: ANN001
    out = {}
    for t in trace_ids:
        if not t:
            continue
        r = client.get(f"/api/runs/{t}")
        if r.status_code == 200:
            out[t] = r.json()
    return out


# ---------- 编排 ----------

def run_eval(
    cases_path: str | Path,
    *,
    baselines: tuple[str, ...] = ("current",),
    include_hidden: bool = False,
    llm_mode: str = "auto",  # auto | fake | real
) -> dict:
    """进程内执行评测（假设 app/DB 已就绪）；返回可序列化结果并落报告。"""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.db import models  # noqa: F401 —— 注册全部表
    from app.db.base import Base, engine
    from app.eval import baseline_a, report
    from app.eval.cases import load_eval_cases
    from app.eval.graders import CaseResult
    from app.llm.adapter import get_llm, llm_available
    from app.main import app

    meta, cases = load_eval_cases(cases_path)
    selected = [c for c in cases if include_hidden or not c.hidden]

    use_real = (
        llm_mode == "real"
        or (llm_mode == "auto" and llm_available())
    )
    if llm_mode == "real" and not llm_available():
        raise SystemExit("--real 需要 LLM_API_KEY（当前不可用）")

    Base.metadata.create_all(engine)
    settings = get_settings()

    columns: dict[str, list[CaseResult]] = {}
    with TestClient(app) as client:
        for col in baselines:
            if col == "current":
                columns["current"] = _run_column(
                    client, app, selected, use_scripts=not use_real)
            elif col == "B":
                with _with_memory_injection(False):
                    columns["B"] = _run_column(
                        client, app, selected, use_scripts=not use_real)
            elif col == "A":
                if not use_real:
                    columns["A"] = _skip_column(
                        selected, "跳过：基线 A 需要真实 LLM（fake 模式无意义）")
                else:
                    columns["A"] = baseline_a.run(selected, get_llm())
            else:
                raise SystemExit(f"未知基线: {col}（current | A | B）")

    aggregates = {
        name: report.aggregate_column(results) for name, results in columns.items()
    }
    report_path = report.write_report(
        meta, columns, aggregates,
        git_sha=settings.git_sha,
        model=(get_llm().model if use_real else "scripted-fake"),
        real_llm=use_real,
        include_hidden=include_hidden,
    )
    if "current" in columns:
        report.persist_results(settings.git_sha, columns["current"])
    return {
        "meta": meta.model_dump(),
        "git_sha": settings.git_sha,
        "report_path": str(report_path),
        "columns": {k: [report.case_to_dict(c) for c in v] for k, v in columns.items()},
        "aggregates": aggregates,
        "summary": report.summary_line(columns, aggregates),
    }


def _run_column(client, app, cases, *, use_scripts: bool):  # noqa: ANN001
    from app.eval.cases import ChatCase, PlanCase
    from app.eval.graders import CaseResult, grade_chat_case, grade_plan_case

    results: list[CaseResult] = []
    for case in cases:
        if isinstance(case, PlanCase):
            body, err = _run_plan_case(client, case)
            results.append(grade_plan_case(case, body, err))
        elif isinstance(case, ChatCase):
            dones, err = _run_chat_case(client, app, case, use_scripts=use_scripts)
            runs = _spans_for_traces(client, [d.get("trace_id", "") for d in dones])
            results.append(grade_chat_case(case, dones, err, runs))
    return results


def _skip_column(cases, reason: str) -> list:  # noqa: ANN001
    from app.eval.graders import CaseResult
    return [CaseResult(c.case_id, c.hidden, getattr(c, "kind", "plan"),
                       passed=False, failures=[reason]) for c in cases]


# ---------- CLI ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="TripAgent eval runner（§7.3；报告 reports/eval-<sha>.md）")
    ap.add_argument("--cases", default=str(REPO_ROOT / "data" / "eval_cases.jsonl"))
    ap.add_argument("--baseline", default="current",
                    help="current | A | B | all（逗号分隔；默认 current）")
    ap.add_argument("--include-hidden", action="store_true",
                    help="含 hidden 用例（最终验收口径，§14 防过拟合）")
    ap.add_argument("--real", action="store_true", help="强制真实 LLM（默认 auto）")
    ap.add_argument("--fake", action="store_true", help="强制脚本替身（CI 确定性）")
    ap.add_argument("--json", help="机器可读结果输出路径（可选）")
    ap.add_argument("--ci", action="store_true",
                    help="CI 回归模式：fake + 公开集 current,B + 对比 reports/baseline.json，"
                         "硬约束类退化或 final_pass_rate 降 >2pp 即 exit 1（§12）")
    ap.add_argument("--update-baseline", action="store_true",
                    help="用本次 current 聚合刷新 reports/baseline.json（指标有意提升后显式提交）")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="tripagent-eval-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(tmp / 'eval.db').as_posix()}")
    sys.path.insert(0, str(REPO_ROOT))

    baselines = ("current", "B") if args.ci else tuple(
        s.strip() for s in args.baseline.split(","))
    if baselines == ("all",):
        baselines = ("current", "A", "B")
    llm_mode = "fake" if (args.ci or args.fake) else ("real" if args.real else "auto")

    result = run_eval(
        args.cases,
        baselines=baselines,
        include_hidden=args.include_hidden,
        llm_mode=llm_mode,
    )

    from app.eval import report

    for col, results in result["columns"].items():
        for c in results:
            mark = "PASS" if c["passed"] else "FAIL"
            extra = f" -> {c['failures']}" if c["failures"] else ""
            print(f"[{mark}] ({col}) {c['case_id']}{' (hidden)' if c['hidden'] else ''}{extra}")
    print(f"\n{result['summary']}")
    print(f"report -> {result['report_path']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"results -> {args.json}")

    regressions: list[str] = []
    if args.update_baseline:
        path = report.write_baseline(result["aggregates"]["current"],
                                     result["git_sha"], result["meta"]["version"])
        print(f"baseline 已刷新 -> {path}")
    if args.ci:
        regressions = report.compare_baseline(result["aggregates"]["current"])
        for msg in regressions:
            print(f"REGRESSION: {msg}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
