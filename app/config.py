"""应用配置（§11.2 环境变量 + 版本信息）。

优先级：真实环境变量 > .env 文件 > 内置默认值。
不引入 python-dotenv，保持 §17 依赖清单不变。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """极简 .env 解析（KEY=VALUE，忽略注释与空行）。"""
    path = path or _REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _git_sha() -> str:
    """GIT_SHA 环境变量优先（Docker build / CI 注入）；开发环境读 git。"""
    sha = os.environ.get("GIT_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 —— 非 git 环境（如容器内源码拷贝）
        return "unknown"


@dataclass(frozen=True)
class Settings:
    # LLM（B3：DeepSeek，OpenAI-compatible，适配器可插拔）
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    # 路由 / 距离矩阵（B7：默认读预计算 matrix.json）
    routing_provider: str
    osrm_base_url: str
    amap_key: str
    # 数据与运行（§11.2）
    database_url: str
    eval_cases_path: str
    log_level: str
    # 基线 B 开关（ADR-006：完整管线关闭记忆注入；on/off）
    memory_injection: bool
    # Registry 心跳间隔秒（§7.4 默认 30；测试会话停表防与状态断言竞态）
    registry_heartbeat_interval_s: float
    # 版本可追溯（§11.4：/healthz 返回 version + git_sha）
    version: str
    git_sha: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    file_env = load_env_file()

    def val(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_env.get(key) or default

    return Settings(
        llm_base_url=val("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_model=val("LLM_MODEL", "deepseek-v4-pro"),
        llm_api_key=val("LLM_API_KEY"),
        routing_provider=val("ROUTING_PROVIDER", "static"),
        osrm_base_url=val("OSRM_BASE_URL"),
        amap_key=val("AMAP_KEY"),
        database_url=val("DATABASE_URL", "sqlite:///./data/tripagent.db"),
        eval_cases_path=val("EVAL_CASES_PATH", "./data/eval_cases.jsonl"),
        log_level=val("LOG_LEVEL", "INFO"),
        memory_injection=val("MEMORY_INJECTION", "on").strip().lower()
        not in {"off", "0", "false", "no"},
        registry_heartbeat_interval_s=float(val("REGISTRY_HEARTBEAT_INTERVAL_S", "30")),
        version=__version__,
        git_sha=_git_sha(),
    )
