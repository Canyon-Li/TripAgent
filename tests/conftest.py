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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    from app.db import models  # noqa: F401  —— 确保 Base 注册全部表
    from app.db.base import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)  # 测试库直接建表（与 Alembic 迁移同构）
    with TestClient(app) as c:
        yield c
