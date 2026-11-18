"""/healthz /readyz 探针（§10：返回 version 与 git_sha）。

阶段 4 起 /readyz 增 registry 检查：主链路关键 kind（solver / matrix / verifier）
各需 ≥1 张 active 卡，否则 not_ready（心跳摘除的降级信号，§7.4 / §15）。
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import get_settings
from app.db.base import engine
from app.registry import service as registry_service

router = APIRouter()

_CRITICAL_KINDS = ("solver", "tool", "verifier")  # matrix 注册为 tool 桶（§9 kind 枚举）


@router.get("/healthz")
def healthz() -> dict:
    s = get_settings()
    return {"status": "ok", "version": s.version, "git_sha": s.git_sha}


def _registry_check() -> str:
    missing = [k for k in _CRITICAL_KINDS if not registry_service.discover(kind=k)]
    if missing:
        return f"error: no active capability for kind(s): {', '.join(missing)}"
    return "ok"


@router.get("/readyz")
def readyz(response: Response) -> dict:
    s = get_settings()
    checks: dict[str, str] = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001 —— 探针把失败原因原样带回
        checks["database"] = f"error: {e}"
    checks["registry"] = _registry_check()
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {
        "status": "ready" if ok else "not_ready",
        "version": s.version,
        "git_sha": s.git_sha,
        "checks": checks,
    }
