"""LLM 适配器（§7.5 / B3 / B17）：OpenAI-compatible 统一接口、错误模型、重试。

职责边界（ADR-008）：adapter 只负责「可靠地拿到模型输出」——超时/重试/token 统计；
tool_calls 的 arguments **不做任何解析校验**，原始 JSON 字符串原样上抛，由
Resolver 的白名单护栏（runtime/resolver.py）强校验。LLM 失败的降级策略也在
Resolver（规则模板），本模块只抛类型化异常。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import openai

from app.config import get_settings


class LLMError(Exception):
    """不可重试失败（鉴权 / 参数 / 响应异常）——调用方应走降级路径。"""


class LLMRetryableError(Exception):
    """可重试失败（网络 / 超时 / 限流 / 5xx）——重试耗尽后仍归调用方降级。"""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str  # 原始 JSON 字符串；解析与强校验在 Resolver 护栏（§7.6）


@dataclass(frozen=True)
class LLMResult:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    token_in: int = 0
    token_out: int = 0
    model: str = ""
    finish_reason: str = ""


class LLMAdapter(Protocol):
    """可插拔接口：DeepSeek / 任意 OpenAI-compatible / 测试替身（B3）。"""

    def chat(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult: ...


def map_openai_error(exc: Exception) -> Exception:
    """openai SDK 异常 → 统一错误模型（§7.5 可重试 / 不可重试）。"""
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return LLMRetryableError(f"connection: {exc}")
    if isinstance(exc, openai.RateLimitError):
        return LLMRetryableError(f"rate_limited: {exc}")
    if isinstance(exc, openai.InternalServerError):
        return LLMRetryableError(f"server_error: {exc}")
    return LLMError(f"{type(exc).__name__}: {exc}")


class DeepSeekAdapter:
    """DeepSeek（OpenAI-compatible）实现；tools 参数原生透传（B17）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        max_retries: int = 1,
        client: Any | None = None,  # 测试注入；None 时自建
    ) -> None:
        if not api_key:
            raise LLMError("LLM_API_KEY 未配置（B3）：请检查 .env")
        self._model = model
        self._max_retries = max_retries
        self._client = client or openai.OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0
        )

    def chat(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        kwargs: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                return self._parse(resp)
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 统一映射 openai 异常族
                mapped = map_openai_error(exc)
                if isinstance(mapped, LLMError) or attempt == self._max_retries:
                    raise mapped from exc
                last_error = mapped
                time.sleep(0.5)  # 退避后重试（仅一次，§14 控制评测成本）
        raise last_error or LLMError("unreachable")

    @staticmethod
    def _parse(resp: Any) -> LLMResult:
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = tuple(
            ToolCall(id=tc.id, name=tc.function.name, arguments_json=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
        )
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=msg.content or "",
            tool_calls=tool_calls,
            token_in=getattr(usage, "prompt_tokens", 0) or 0,
            token_out=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(resp, "model", "") or "",
            finish_reason=choice.finish_reason or "",
        )


def llm_available() -> bool:
    """是否具备真实 LLM 调用条件（无 key 时 chat 主链路直接走规则模板降级）。"""
    return bool(get_settings().llm_api_key)


def get_llm() -> LLMAdapter:
    """FastAPI 依赖注入口（tests 用 dependency_overrides 换 FakeLLM，CI 零外呼）。"""
    s = get_settings()
    return DeepSeekAdapter(
        base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model
    )


@dataclass
class UsageMeter:
    """跨多次 LLM 调用累计 token（一次 /api/chat 的 cost 汇总）。"""

    token_in: int = 0
    token_out: int = 0
    calls: int = 0
    errors: int = 0
    model: str = ""
    degraded: bool = False

    def add(self, r: LLMResult) -> None:
        self.token_in += r.token_in
        self.token_out += r.token_out
        self.calls += 1
        if r.model:
            self.model = r.model

    def mark_degraded(self) -> None:
        self.degraded = True
        self.errors += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "token_in": self.token_in,
            "token_out": self.token_out,
            "llm_calls": self.calls,
            "llm_errors": self.errors,
            "model": self.model,
            "degraded": self.degraded,
        }
