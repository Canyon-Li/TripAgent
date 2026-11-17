"""线上坏例 → 评测用例（§7.3 回归闭环末环：「线上坏例可一键转为新评测用例」）。

从 runs 表读一条 trace 的 request/response，生成 plan 用例草稿；
断言草稿以「当前系统行为」为答案基线——入库前须人工确认这是期望行为，
否则测试会自证清白（脚本与 CLI 均显著提示）。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.eval.cases import load_eval_cases


def fetch_run(trace_id: str) -> dict | None:
    """读 run（request + response_json；spans 不需要）。"""
    from app.db.base import SessionLocal
    from app.db.models import Run

    with SessionLocal() as db:
        run = db.get(Run, trace_id)
        if run is None:
            return None
        return {
            "trace_id": run.trace_id,
            "session_id": run.session_id,
            "status": run.status,
            "request": json.loads(run.request_json),
            "response": json.loads(run.response_json) if run.response_json else None,
        }


def build_case(run: dict) -> dict:
    """run（fetch_run 产物）→ plan 用例草稿 dict（未入库）。

    chat run（request 含 message）：用末轮 applied_constraints 回显的合并终值
    构造结构化请求——即「这条坏例对应的那组生效约束」。
    """
    req, resp = run["request"], run["response"] or {}
    if "message" in req:  # chat run → 从回显的合并终值还原结构化请求
        echo = {c["field"]: c["value"] for c in
                resp.get("applied_constraints", {}).get("constraints", [])}
        plan_req = {
            "city": "beijing",
            "days": int(echo.get("days", 3)),
            "hotel": echo.get("hotel", "wangfujing"),
            "budget": float(echo.get("budget", 0)),
            "daily_minutes_cap": int(echo.get("daily_minutes_cap", 480)),
            "must_visit": list(echo.get("must_visit", [])),
            "preferences": list(echo.get("preferences", [])),
            "session_id": "badcase",
            "start_date": (resp.get("days") or [{}])[0].get("date")
            or req.get("start_date"),
        }
        if not plan_req["start_date"]:
            raise ValueError("chat run 缺 start_date，无法构造确定性用例")
    else:
        plan_req = dict(req)

    status = resp.get("status", "infeasible")
    asserts: dict = {"status": status}
    if status == "feasible":
        asserts.update({
            "must_visit_covered": True,
            "budget_within": True,
            "time_window_violations": len(resp.get("verification", {}).get("violations") or []),
        })
    else:
        asserts["reason_contains"] = [
            r.split(":")[0] for r in resp.get("infeasible_reasons", [])][:2]

    return {
        "case_id": f"badcase_{run['trace_id'].removeprefix('tr-')}",
        "hidden": False,
        "kind": "plan",
        "request": plan_req,
        "asserts": asserts,
        # 人工确认提示（append 时剔除；pydantic 忽略未知字段，草稿可直接 load 校验）
        "_from_trace": run["trace_id"],
    }


def append_case(cases_path: str | Path, case: dict, *, hidden: bool = False) -> Path:
    """草稿追加进评测集并同步 _meta 计数；随后整文件重新加载校验（原子性兜底）。"""
    path = Path(cases_path)
    case = {k: v for k, v in case.items() if not k.startswith("_")}
    case["hidden"] = hidden
    lines = path.read_text(encoding="utf-8").splitlines()

    def _meta_line() -> tuple[int, dict]:
        for i, line in enumerate(lines):
            if line.strip().startswith('{"_meta'):
                return i, json.loads(line)
        raise ValueError(f"评测集缺 _meta 首行: {path}")

    idx, meta = _meta_line()
    meta["_meta"]["count"] += 1
    meta["_meta"]["public"] += 0 if hidden else 1
    meta["_meta"]["hidden"] += 1 if hidden else 0
    lines[idx] = json.dumps(meta, ensure_ascii=False)
    new_text = "\n".join(lines) + "\n" + json.dumps(case, ensure_ascii=False) + "\n"
    path.write_text(new_text, encoding="utf-8")
    meta_obj, cases = load_eval_cases(path)  # 校验：追加后仍可完整加载
    assert len(cases) == meta_obj.count, "追加后计数不一致"
    return path
