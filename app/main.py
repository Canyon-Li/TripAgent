"""FastAPI 入口（阶段 4：+ registry / agent-card 路由与 lifespan 心跳）。"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import agent_card, chat, health, memory, plan, registry
from app.api import eval as eval_api
from app.config import get_settings
from app.db import runs as runs_db
from app.registry import health as registry_health
from app.registry import service as registry_service

# 本机调试来源白名单（compose 下走 nginx 同源反代，不触发 CORS）
_WEB_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """启动序列（§7.4 / §7.1）：种子 → 清扫 stale run → 首跳 → 30s 心跳常驻。

    TestClient 不进 context manager 不触发 lifespan——测试确定性不受常驻任务影响，
    心跳逻辑由测试直接调 run_heartbeat() 覆盖。
    """
    registry_service.ensure_seed()
    swept = runs_db.mark_stale_running_interrupted()
    if swept:
        logging.getLogger("tripagent").info(
            "startup: %d stale running run(s) -> interrupted", swept)
    registry_health.run_heartbeat()
    task = asyncio.create_task(registry_health.heartbeat_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    app = FastAPI(
        title="TripAgent",
        version=__version__,
        description="多约束行程 Agent 可靠性平台（设计见 project.md）",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_WEB_ORIGIN_REGEX,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(plan.router)
    app.include_router(chat.router)
    app.include_router(memory.router)
    app.include_router(eval_api.router)
    app.include_router(registry.router)
    app.include_router(agent_card.router)
    return app


app = create_app()
