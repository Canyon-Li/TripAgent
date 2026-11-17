"""2.5 LLM 适配器：错误映射、重试一次、tool_calls 原样上抛、依赖注入口。"""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from app.llm import adapter
from app.llm.adapter import LLMResult
from tests.fakes import FakeLLM, tool_call_result


def _status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    """openai SDK 状态异常需要真实 httpx.Response 才能构造。"""
    req = httpx.Request("POST", "https://fake/v1/chat/completions")
    return cls(message="m", response=httpx.Response(status, request=req), body=None)


class _FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)

    def create(self, **_kwargs):  # noqa: ANN003, ANN201
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(outcomes)


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message, finish_reason="tool_calls"):
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 11
    completion_tokens = 7


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]
        self.usage = _Usage()
        self.model = "deepseek-fake"


class _TC:
    def __init__(self, cid, name, args):
        self.id = cid
        fn = type("Fn", (), {})
        fn.name = name
        fn.arguments = args
        self.function = fn


def _adapter(client) -> adapter.DeepSeekAdapter:
    return adapter.DeepSeekAdapter(
        base_url="https://fake", api_key="k", model="m", client=client
    )


def test_parse_response_with_tool_calls_raw_json_passthrough():
    resp = _Resp(_Msg(None, [_TC("c1", "submit_plan_request",
                                  json.dumps({"days": 3, "budget": 100}))]))
    got = _adapter(_FakeClient([resp])).chat(messages=[{"role": "user", "content": "hi"}])
    assert got.tool_calls[0].name == "submit_plan_request"
    # arguments 原样字符串，不解析（护栏职责在 Resolver，ADR-008）
    assert json.loads(got.tool_calls[0].arguments_json)["budget"] == 100
    assert got.token_in == 11 and got.token_out == 7 and got.model == "deepseek-fake"


def test_retry_once_on_connection_error_then_success():
    err = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    resp = _Resp(_Msg("ok", None))
    got = _adapter(_FakeClient([err, resp])).chat(messages=[])
    assert got.text == "ok"


def test_auth_error_raises_fatal_immediately(monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda _: None)
    err = _status_error(openai.AuthenticationError, 401)
    a = _adapter(_FakeClient([err, err]))
    with pytest.raises(adapter.LLMError, match="AuthenticationError"):
        a.chat(messages=[])


def test_retry_exhausted_raises_retryable(monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda _: None)
    err = openai.APITimeoutError(request=None)  # type: ignore[arg-type]
    a = _adapter(_FakeClient([err, err]))
    with pytest.raises(adapter.LLMRetryableError, match="connection"):
        a.chat(messages=[])


def test_missing_api_key_rejected():
    with pytest.raises(adapter.LLMError, match="LLM_API_KEY"):
        adapter.DeepSeekAdapter(base_url="x", api_key="", model="m")


def test_map_error_classification():
    assert isinstance(
        adapter.map_openai_error(_status_error(openai.RateLimitError, 429)),
        adapter.LLMRetryableError,
    )
    assert isinstance(
        adapter.map_openai_error(ValueError("boom")), adapter.LLMError
    )


def test_fake_llm_records_and_raises():
    fake = FakeLLM([tool_call_result(("ask_clarification", '{"field": "budget"}')),
                    adapter.LLMRetryableError("down")])
    r1 = fake.chat(messages=[{"role": "user", "content": "a"}], tools=[])
    assert r1.tool_calls[0].name == "ask_clarification"
    with pytest.raises(adapter.LLMRetryableError):
        fake.chat(messages=[{"role": "user", "content": "b"}])
    assert len(fake.calls) == 2 and fake.last_messages[0]["content"] == "b"


def test_usage_meter():
    m = adapter.UsageMeter()
    m.add(tool_call_result(("submit_plan_request", "{}")))
    m.add(LLMResult(text="x", token_in=5, token_out=3, model="mm"))
    m.mark_degraded()
    d = m.as_dict()
    assert d == {"token_in": 105, "token_out": 23, "llm_calls": 2,
                 "llm_errors": 1, "model": "mm", "degraded": True}
