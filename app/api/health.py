"""/healthz /readyz 探针（§10：返回 version 与 git_sha）。"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import get_settings
from app.db.base import engine

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    s = get_settings()
    return {"status": "ok", "version": s.version, "git_sha": s.git_sha}


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
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {
        "status": "ready" if ok else "not_ready",
        "version": s.version,
        "git_sha": s.git_sha,
        "checks": checks,
    }
