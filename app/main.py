"""FastAPI 入口（阶段 0 骨架：探针就绪；/api/* 路由随阶段 1–4 落地）。"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import get_settings


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    app = FastAPI(
        title="TripAgent",
        version=__version__,
        description="多约束行程 Agent 可靠性平台（设计见 project.md）",
    )
    app.include_router(health.router)
    return app


app = create_app()
