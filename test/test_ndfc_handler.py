"""NDFC ``after_llm_request`` 事件处理器测试。"""

from __future__ import annotations

from typing import Any

import pytest

from plugins.response_guard.config import ResponseGuardConfig
from plugins.response_guard.handlers.after_llm_request import (
    _NDFC_REQUEST_NAME,
    AfterLLMRequestGuardHandler,
)
from plugins.response_guard.quarantine import quarantine_snapshot
from src.kernel.event import EventDecision

_REFUSAL_MESSAGE = "抱歉，我无法提供这类内容。"
_CALLS = [{"id": "call-1", "name": "action-send_text", "args": {"content": _REFUSAL_MESSAGE}}]


class _FakePlugin:
    """最小化插件替身，只提供配置读取。"""

    def __init__(self, config: ResponseGuardConfig) -> None:
        self.config = config


def _build_config(**overrides: Any) -> ResponseGuardConfig:
    """构造带指定覆盖项的配置。"""
    return ResponseGuardConfig(
        response_guard=ResponseGuardConfig.GuardSection(**overrides)
    )


def _handler(**overrides: Any) -> AfterLLMRequestGuardHandler:
    """构造绑定到替身插件的处理器。"""
    return AfterLLMRequestGuardHandler(_FakePlugin(_build_config(**overrides)))


def _params(**overrides: Any) -> dict[str, Any]:
    """构造事件参数。"""
    params: dict[str, Any] = {
        "request_name": _NDFC_REQUEST_NAME,
        "model_identifier": "test-model",
        "stream": False,
        "success": True,
        "message": _REFUSAL_MESSAGE,
        "reasoning_content": None,
        "reasoning_parts": [],
        "tool_calls": list(_CALLS),
    }
    params.update(overrides)
    return params


async def test_other_request_is_ignored() -> None:
    """其它 chatter 的请求不受本处理器影响。"""
    params = _params(request_name="kokoro_flow_chatter")
    decision, returned = await _handler().execute("after_llm_request", params)
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE
    assert returned["tool_calls"] == _CALLS


async def test_passing_response_is_not_modified() -> None:
    """正常回复不修改任何事件参数。"""
    params = _params(message="好呀，那我们继续。", tool_calls=[])
    decision, returned = await _handler().execute("after_llm_request", params)
    assert decision is EventDecision.PASS
    assert returned["message"] == "好呀，那我们继续。"
    assert returned["reasoning_content"] is None
    assert returned["reasoning_parts"] == []
    assert returned["tool_calls"] == []


async def test_model_refusal_clears_response_fields() -> None:
    """命中时清空四个响应字段并回写。"""
    params = _params(reasoning_content="安全政策不允许此类内容，我必须拒绝。")
    decision, returned = await _handler().execute("after_llm_request", params)
    assert decision is EventDecision.SUCCESS
    assert returned["message"] is None
    assert returned["reasoning_content"] is None
    assert returned["reasoning_parts"] == []
    assert returned["tool_calls"] == []


async def test_model_refusal_is_quarantined() -> None:
    """命中会写入隔离记录。"""
    before = len(quarantine_snapshot())
    await _handler().execute("after_llm_request", _params())
    records = quarantine_snapshot()
    assert len(records) == before + 1
    assert records[-1].request_name == _NDFC_REQUEST_NAME
    assert "reply_platform_refusal" in records[-1].evidence
    assert records[-1].reply_excerpt is None


async def test_stream_response_is_skipped() -> None:
    """流式响应在事件阶段还没有内容，必须跳过。"""
    params = _params(stream=True)
    decision, returned = await _handler().execute("after_llm_request", params)
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE
    assert returned["tool_calls"] == _CALLS


async def test_handler_disabled_by_config() -> None:
    """关闭 NDFC 拦截后不做任何处理。"""
    params = _params()
    decision, returned = await _handler(guard_ndfc=False).execute(
        "after_llm_request", params
    )
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE


async def test_handler_disabled_by_master_switch() -> None:
    """总开关关闭后不做任何处理。"""
    params = _params()
    decision, returned = await _handler(enabled=False).execute(
        "after_llm_request", params
    )
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE


async def test_unknown_config_type_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置类型不可用时放行。"""
    handler = AfterLLMRequestGuardHandler(_FakePlugin(object()))  # type: ignore[arg-type]
    params = _params()
    decision, returned = await handler.execute("after_llm_request", params)
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE


async def test_detection_failure_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """检测核心异常时放行，不影响聊天链路。"""
    from plugins.response_guard.handlers import after_llm_request as module

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(module, "run_guard", _boom)
    params = _params()
    decision, returned = await _handler().execute("after_llm_request", params)
    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL_MESSAGE
