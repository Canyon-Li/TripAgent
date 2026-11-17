"""结构化检查脚本（TASKS 1.7 → 阶段 3）：批量跑 plan 用例并断言。

用法：
    .venv/Scripts/python.exe scripts/check_plans.py                  # 默认 eval_cases.jsonl
    .venv/Scripts/python.exe scripts/check_plans.py --include-hidden # 全量（最终验收口径，§14）
    .venv/Scripts/python.exe scripts/check_plans.py --json out.json  # 机器可读结果

断言口径唯一来源 = app/eval/graders.grade_plan_case（与 eval runner 同一套，
含 §12 的预算回显一致性与硬约束/行为分类）；chat 用例归 eval runner，此处跳过。
运行方式：进程内 TestClient 走完整 HTTP 栈（含 422 校验与 runs/spans 落库），
DATABASE_URL 指向临时库，不污染 data/tripagent.db。任一用例失败 exit 1。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_TMP = Path(tempfile.mkdtemp(prefix="tripagent-check-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TMP / 'check.db').as_posix()}")
sys.path.insert(0, str(REPO_ROOT))


def run_checks(cases: list[dict], include_hidden: bool) -> list[dict]:
    from fastapi.testclient import TestClient

    from app.db import models  # noqa: F401 —— 注册全部表
    from app.db.base import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)

    results: list[dict] = []
    with TestClient(app) as client:
        for case in cases:
            if case.get("hidden") and not include_hidden:
                continue
            results.append(_run_case(client, case))
    return results


def _run_case(client, case: dict) -> dict:  # noqa: ANN001 —— TestClient
    from app.eval.cases import PlanCase
    from app.eval.graders import grade_plan_case

    case_id = case["case_id"]
    r = client.post("/api/plan", json=case["request"])

    if r.status_code != 200:
        return {
            "case_id": case_id,
            "hidden": bool(case.get("hidden")),
            "passed": False,
            "failures": [f"HTTP {r.status_code}: {r.text[:200]}"],
        }

    body = r.json()
    graded = grade_plan_case(
        PlanCase.model_validate({**case, "kind": "plan"}), body, None)
    return {
        "case_id": case_id,
        "hidden": bool(case.get("hidden")),
        "passed": graded.passed,
        "status": body["status"],
        "failures": graded.failures,
        "latency_ms": body["cost"]["latency_ms"],
        "recomputed_budget": body["verification"]["recomputed_budget"],
        "trace_id": body["trace_id"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default=str(REPO_ROOT / "data" / "eval_cases.jsonl"))
    ap.add_argument("--include-hidden", action="store_true",
                    help="包含 hidden 用例（最终验收口径；开发调试默认跳过，§14 防过拟合）")
    ap.add_argument("--json", help="机器可读结果输出路径（可选）")
    args = ap.parse_args()

    cases = [
        json.loads(line)
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    cases = [c for c in cases if "_meta" not in c]  # 阶段 3：跳过评测集版本头
    cases = [c for c in cases if c.get("kind", "plan") == "plan"]  # chat 用例归 eval runner
    results = run_checks(cases, include_hidden=args.include_hidden)

    n_pass = sum(1 for r in results if r["passed"])
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        extra = f" -> {r['failures']}" if r["failures"] else ""
        spent = ""
        if r.get("status") == "feasible":
            spent = f" 花费 {r.get('recomputed_budget', '-')}元"
        print(f"[{mark}] {r['case_id']}{' (hidden)' if r['hidden'] else ''}"
              f" ({r.get('status', 'error')}, {r.get('latency_ms', '-')}ms{spent}){extra}")

    total = len(results)
    print(f"\n{n_pass}/{total} passed"
          + (f"（另 {sum(1 for c in cases if c.get('hidden'))} 条 hidden 未运行）"
             if not args.include_hidden and any(c.get("hidden") for c in cases) else ""))

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"results -> {args.json}")

    return 0 if n_pass == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
