"""评测报告（§7.3 回归闭环）：reports/eval-<sha>.md 三列 + eval_results 入库 + baseline.json。

报告必须注明 git_sha、模型名与评测集版本（§12 可复现）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from app.eval import graders
from app.eval.cases import EvalSetMeta
from app.eval.graders import CaseResult

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "reports"
BASELINE_PATH = REPORTS_DIR / "baseline.json"

METRIC_ORDER = (
    graders.M_HARD, graders.M_TIMEWIN, graders.M_BUDGET, graders.M_RECALL,
    graders.M_TOOL_FC, graders.M_TOOL_CAP, graders.M_P95, graders.M_COST,
    graders.M_FINAL,
)
METRIC_LABELS = {
    graders.M_HARD: "硬约束通过率",
    graders.M_TIMEWIN: "时间窗冲突总数",
    graders.M_BUDGET: "预算准确率",
    graders.M_RECALL: "memory_recall（chat 用例）",
    graders.M_TOOL_FC: "工具成功率·function calling",
    graders.M_TOOL_CAP: "工具成功率·capability",
    graders.M_P95: "P95 延迟 ms（记录型）",
    graders.M_COST: "单均 token 成本（记录型）",
    graders.M_FINAL: "final_pass_rate",
}

# 指标聚合形态（看板/报告共用，避免前端按指标名重推导）：rate=通过数/适用数，
# sum=逐用例值求和（冲突总数），mean=逐用例值均值（延迟/成本记录型）
METRIC_AGGS = {
    graders.M_HARD: "rate", graders.M_TIMEWIN: "sum", graders.M_BUDGET: "rate",
    graders.M_RECALL: "rate", graders.M_TOOL_FC: "rate", graders.M_TOOL_CAP: "rate",
    graders.M_P95: "mean", graders.M_COST: "mean", graders.M_FINAL: "rate",
}


def metrics_meta() -> list[dict]:
    """指标元数据（GET /api/eval/reports 下发，前端下拉与图表不再各自硬编码）。"""
    return [
        {"name": m, "label": METRIC_LABELS[m], "agg": METRIC_AGGS[m]}
        for m in METRIC_ORDER
    ]


def case_to_dict(c: CaseResult) -> dict:
    return {
        "case_id": c.case_id, "hidden": c.hidden, "kind": c.kind,
        "passed": c.passed, "failures": c.failures,
        "latency_ms": c.latency_ms, "token_in": c.token_in, "token_out": c.token_out,
        "trace_ids": c.trace_ids,
        "metrics": {k: {"passed": p, "value": v} for k, (p, v) in c.metrics.items()},
    }


def _fmt(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float) and v >= 1000:
        return f"{v:,.0f}"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _eval_set_digest(path_str: str | None) -> str:
    if not path_str or not Path(path_str).exists():
        return "-"
    return hashlib.sha256(Path(path_str).read_bytes()).hexdigest()[:12]


def write_report(
    meta: EvalSetMeta,
    columns: dict[str, list[CaseResult]],
    aggregates: dict[str, dict[str, float]],
    *,
    git_sha: str,
    model: str,
    real_llm: bool,
    include_hidden: bool,
    cases_path: str | None = None,
    reports_dir: Path | None = None,
) -> Path:
    reports_dir = REPORTS_DIR if reports_dir is None else reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"eval-{git_sha}.md"
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    scope = "40 条全集（含 hidden）" if include_hidden else "公开用例（hidden 不参与，§14）"

    lines = [
        f"# 评测报告 · {git_sha}",
        "",
        f"- 生成时间：{now}",
        f"- git_sha：`{git_sha}`；模型：`{model}`"
        f"（{'真实 LLM' if real_llm else '脚本替身/无 LLM'}）",
        f"- 评测集：v{meta.version}（{meta.count} 条 = 公开 {meta.public} + 隐藏 {meta.hidden}），"
        f"本次运行范围：{scope}",
        f"- 评测集内容摘要（sha256 前 12 位）：{_eval_set_digest(cases_path)}",
        "- 列：current = 现行管线；A = 纯 LLM 直接生成（ADR-006，仅 plan 用例）；"
        "B = 完整管线关闭记忆注入（ADR-006）",
        "",
        "## 指标（§12）",
        "",
        "| 指标 | current | A | B |",
        "| --- | --- | --- | --- |",
    ]
    for m in METRIC_ORDER:
        row = [METRIC_LABELS[m]]
        for col in ("current", "A", "B"):
            agg = aggregates.get(col, {})
            row.append(_fmt(agg.get(m)))
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## 逐用例（final 口径：所有适用断言同时通过）", ""]
    for col in ("current", "A", "B"):
        results = columns.get(col)
        if results is None:
            continue
        lines.append(f"### {col}")
        if not results:
            lines.append("")
            lines.append("*未运行（基线 A 需要真实 LLM；fake/CI 模式跳过）*")
            lines.append("")
            continue
        lines.append("")
        lines.append("| case | hidden | 结果 | 失败原因 |")
        lines.append("| --- | --- | --- | --- |")
        for c in results:
            lines.append(
                f"| {c.case_id} | {'Y' if c.hidden else ''} | "
                f"{'✅' if c.passed else '❌'} | {'；'.join(c.failures) or ''} |")
        lines.append("")

    n_pass = {col: sum(1 for c in rs if c.passed) for col, rs in columns.items()}
    lines.append("**final 通过数**：" + "，".join(
        f"{col} {'未运行' if not rs else f'{n_pass.get(col, 0)}/{len(rs)}'}"
        for col, rs in columns.items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def summary_line(columns: dict[str, list[CaseResult]],
                 aggregates: dict[str, dict[str, float]]) -> str:
    parts = []
    for col, rs in columns.items():
        if not rs:
            parts.append(f"{col}: 未运行（需要真实 LLM）")
            continue
        n = sum(1 for c in rs if c.passed)
        rate = aggregates.get(col, {}).get(graders.M_FINAL, float("nan"))
        parts.append(f"{col}: {n}/{len(rs)}（final_pass_rate {_fmt(rate)}）")
    return "；".join(parts)


def persist_results(git_sha: str, results: list[CaseResult]) -> None:
    """current 列逐用例×指标入 eval_results（看板趋势数据源）。"""
    from app.db import evals as evals_db

    for c in results:
        evals_db.save_eval_results(
            git_sha, c.case_id,
            [(m, p, v) for m, (p, v) in c.metrics.items()],
        )


def write_baseline(current_aggregates: dict[str, float], git_sha: str,
                   eval_set_version: str, path: Path | None = None) -> Path:
    path = BASELINE_PATH if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = {m: current_aggregates[m] for m in METRIC_ORDER
               if m in current_aggregates}
    path.write_text(json.dumps({
        "git_sha": git_sha,
        "eval_set_version": eval_set_version,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "metrics": metrics,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_baseline(current_aggregates: dict[str, float],
                     path: Path | None = None) -> list[str]:
    """§12 验收规则：硬约束类指标任一退化即回归；final_pass_rate 降 >2pp 回归。

    返回回归消息列表（空 = 通过）；baseline 缺失视为未初始化（报错）。
    """
    path = BASELINE_PATH if path is None else path
    if not path.exists():
        return [f"基线缺失：{path}——先运行 --update-baseline 生成并提交"]
    baseline = json.loads(path.read_text(encoding="utf-8"))["metrics"]
    msgs: list[str] = []

    for m in graders.HARD_METRICS:
        base, cur = baseline.get(m), current_aggregates.get(m)
        if base is None or cur is None or math.isnan(base) or math.isnan(cur):
            continue
        higher_better = m != graders.M_TIMEWIN  # 冲突数越低越好
        degraded = cur < base - 1e-9 if higher_better else cur > base + 1e-9
        if degraded:
            msgs.append(f"{METRIC_LABELS[m]}（{m}）退化：{_fmt(base)} -> {_fmt(cur)}")

    base_final = baseline.get(graders.M_FINAL)
    cur_final = current_aggregates.get(graders.M_FINAL)
    if (base_final is not None and cur_final is not None
            and not math.isnan(base_final) and not math.isnan(cur_final)
            and cur_final < base_final - graders.FINAL_DROP_TOLERANCE):
        msgs.append(f"final_pass_rate 下降超阈值（{_fmt(base_final)} -> "
                    f"{_fmt(cur_final)}，容差 {graders.FINAL_DROP_TOLERANCE:.0%}）")
    return msgs
