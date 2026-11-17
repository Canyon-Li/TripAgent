"""测试替身（不进运行时链路）：脚本化 LLM、可控响应对象。"""

from __future__ import annotations

from typing import Any

from app.llm.adapter import LLMResult, ToolCall


class FakeLLM:
    """按调用顺序消费预设响应；记录每次收到的 messages/tools 供断言。

    script 元素为 LLMResult（正常返回）或 Exception（模拟失败→触发降级）。
    脚本耗尽后返回空文本结果（避免 IndexError 掩盖真实断言失败）。
    """

    def __init__(self, script: list[LLMResult | Exception] | None = None) -> None:
        self.script: list[LLMResult | Exception] = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def chat(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.script:
            return LLMResult(text="(fake: script exhausted)")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def last_messages(self) -> list[dict[str, Any]]:
        return self.calls[-1]["messages"]


def tool_call_result(*calls: tuple[str, str], text: str = "", model: str = "fake") -> LLMResult:
    """快捷构造：(工具名, arguments JSON) 序列 → LLMResult。"""
    return LLMResult(
        text=text,
        tool_calls=tuple(
            ToolCall(id=f"call_{i}", name=name, arguments_json=args)
            for i, (name, args) in enumerate(calls)
        ),
        token_in=100,
        token_out=20,
        model=model,
        finish_reason="tool_calls" if calls else "stop",
    )
