"""线上坏例一键转评测用例（§7.3 回归闭环；逻辑在 app/eval/badcase.py）。

用法：
    .venv/Scripts/python.exe scripts/badcase_to_eval.py tr-xxxx            # 生成草稿（stdout）
    .venv/Scripts/python.exe scripts/badcase_to_eval.py tr-xxxx --append   # 确认后入库

断言草稿以「当前系统行为」为答案基线——入库前请人工确认这就是期望行为，
否则测试只会自证清白（§14 防过拟合的同一逻辑）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trace_id", help="runs 表中的 trace_id（tr-…）")
    ap.add_argument("--cases", default=str(REPO_ROOT / "data" / "eval_cases.jsonl"))
    ap.add_argument("--append", action="store_true", help="确认草稿后追加进评测集")
    ap.add_argument("--hidden", action="store_true", help="作为隐藏用例入库（§14）")
    args = ap.parse_args()

    from app.eval import badcase

    run = badcase.fetch_run(args.trace_id)
    if run is None:
        print(f"trace 不存在: {args.trace_id}")
        return 1
    if run["response"] is None:
        print(f"run {args.trace_id} 无 response_json（未成功完成），无法生成用例")
        return 1

    case = badcase.build_case(run)
    print(json.dumps(case, ensure_ascii=False, indent=2))
    print(f"\n# 来源 trace={run['trace_id']} status={run['status']} —— "
          "断言以当前行为为基线，确认是期望行为后再 --append")

    if args.append:
        path = badcase.append_case(args.cases, case, hidden=args.hidden)
        print(f"已追加 -> {path}（记得同步评测集版本号 _meta.version 并提交）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
