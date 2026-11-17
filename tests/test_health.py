"""探针契约测试（阶段 0 验收：/healthz 200 且含 version + git_sha）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_version_and_git_sha() -> None:
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["git_sha"]


def test_readyz_checks_database() -> None:
    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
