"""只读 Agent Card（ADR-009 / §7.7）：`GET /.well-known/agent.json`。

静态自描述、discovery-only：不实现 A2A Task 状态机 / message/send / SSE 制品流，
卡片内显式声明；发现之后请改用 `POST /api/plan`（普通 REST）。
字段为 A2A **风格**示意，不声称完整合规（§7.7）。
requestFields 由 `/api/plan` 的请求 schema（PlanRequest.model_fields）生成，
二者字段必须一致——§15 契约测试守住。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_settings
from app.schemas import PlanRequest

router = APIRouter()


@router.get("/.well-known/agent.json")
def agent_card(request: Request) -> dict:
    s = get_settings()
    return {
        "name": "TripAgent",
        "description": "多约束多日行程规划；硬约束由确定性求解器与独立校验器保证",
        "version": s.version,
        "url": str(request.base_url).rstrip("/") + "/api/plan",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "plan_itinerary",
                "name": "多约束行程规划",
                "description": "输入城市 / 天数 / 预算 / 必去点，返回逐日时间线与 violations[]",
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            }
        ],
        "x-tripagent": {
            "scope": "discovery-only",
            "a2aTaskEndpoint": None,
            "note": "本服务不实现 A2A Task 状态机；请改用 POST /api/plan",
            # 由 /api/plan 请求 schema 生成（§7.7「卡片由请求 schema 生成」的机器可核验落点）
            "requestFields": list(PlanRequest.model_fields),
        },
    }
