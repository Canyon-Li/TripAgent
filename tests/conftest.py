"""测试公共夹具：在任何 app 导入前把 DATABASE_URL 指向会话级临时库。

app.db.base 在模块导入时即创建引擎（绑定 settings.database_url），
因此必须在导入 app 之前设置环境变量，避免测试写入真实 data/tripagent.db。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="tripagent-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
# 测试进程永不打真实 LLM（CI 无 key，本地有 .env——用哨兵 key + 不可达端点压住 .env 真值；
# 未经 dependency_overrides 注入 FakeLLM 的路径会立刻连接失败 → 确定性走降级）
os.environ["LLM_API_KEY"] = "test-only-never-call"
os.environ["LLM_BASE_URL"] = "http://llm.invalid.test"
os.environ["LLM_MODEL"] = "test-model"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    """session 级建表（与 Alembic 迁移同构）；不依赖 client 的纯 DB 测试也需要。"""
    from app.db import models  # noqa: F401  —— 确保 Base 注册全部表
    from app.db.base import Base, engine

    Base.metadata.create_all(engine)
    yield


@pytest.fixture(scope="session")
def client(_db) -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c
