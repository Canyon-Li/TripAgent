"""文档同步断言：README 中的测试计数与 pytest 实际收集数一致。

2026-11-18 retro 产出：该数字一天内人工漏更两次（audit P1-1 的 128→133、
阶段 4 的 169→183）——重复发生的人工同步失败交给确定性检查接管。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_readme_test_count_matches_pytest_collection():
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=_REPO, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    m = re.search(r"(\d+) tests? collected", collected.stdout)
    assert m, f"无法从 pytest --collect-only 输出解析数量：{collected.stdout[-200:]}"
    count = int(m.group(1))

    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    line = next(
        (row for row in readme.splitlines()
         if row.lstrip().startswith("tests/") and "pytest（" in row),
        None,
    )
    assert line, "README 结构变了：找不到『tests/ … pytest（… N 项）』行，请更新本测试的匹配规则"
    m2 = re.search(r"(\d+)\s*项）", line)
    assert m2, f"README 行内找不到计数：{line}"
    assert int(m2.group(1)) == count, (
        f"README 测试数过期：写的是 {m2.group(1)}，实际收集 {count} —— 改 README 后再提交"
    )
