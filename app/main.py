"""FastAPI 入口（阶段 2：探针 + plan/chat/runs；评测与注册路由随阶段 3–4 落地）。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import chat, health, memory, plan
from app.config import get_settings

# 本机调试来源白名单（compose 下走 nginx 同源反代，不触发 CORS）
_WEB_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    app = FastAPI(
        title="TripAgent",
        version=__version__,
        description="多约束行程 Agent 可靠性平台（设计见 project.md）",
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
    return app


app = create_app()
