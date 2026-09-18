"""NDFC 守卫重试（external resume）的运行时测试。

覆盖三个层面：

- ``after_llm_request``：拦截后按 stream 预算请求恢复，失败与耗尽时保持拦截；
- ``before_llm_request``：剔除内部恢复标记，并按需注入一次性提醒；
- ``runtime``：按 ``stream_id`` 隔离的状态机与非公开框架入口的容错封装。
"""

from __future__ import annotations

from typing import Any

import pytest

from plugins.response_guard import quarantine
from plugins.response_guard.config import ResponseGuardConfig
from plugins.response_guard.handlers.after_llm_request import (
    _NDFC_REQUEST_NAME,
    AfterLLMRequestGuardHandler,
)
from plugins.response_guard.handlers.before_llm_request import (
    BeforeLLMRequestGuardHandler,
)
from plugins.response_guard.runtime import framework_compat
from plugins.response_guard.runtime.ndfc_retry import (
    NDFC_RESUME_PROMPT,
    NDFC_RETRY_MARKER,
    NDFC_RETRY_REMINDER,
    NDFC_RETRY_SOURCE,
    reset_all_retry_states,
)
from src.app.plugin_system.types import LLMPayload, ROLE, Text
from src.kernel.event import EventDecision

_REFUSAL = "抱歉，我无法提供这类内容。"
_CALLS = [
    {"id": "call-1", "name": "action-send_text", "args": {"content": _REFUSAL}}
]
_STREAM_A = "stream-aaa"
_STREAM_B = "stream-bbb"


class _FakePlugin:
    """最小化插件替身，只提供配置读取。"""

    def __init__(self, config: ResponseGuardConfig) -> None:
        self.config = config


def _config(**overrides: Any) -> ResponseGuardConfig:
    """构造带指定覆盖项的插件配置。"""
    return ResponseGuardConfig(
        response_guard=ResponseGuardConfig.GuardSection(**overrides)
    )


def _after_handler(**overrides: Any) -> AfterLLMRequestGuardHandler:
    """构造绑定到替身插件的 after 处理器。"""
    return AfterLLMRequestGuardHandler(_FakePlugin(_config(**overrides)))


def _before_handler(**overrides: Any) -> BeforeLLMRequestGuardHandler:
    """构造绑定到替身插件的 before 处理器。"""
    return BeforeLLMRequestGuardHandler(_FakePlugin(_config(**overrides)))


def _after_params(stream_id: str | None = _STREAM_A, **overrides: Any) -> dict[str, Any]:
    """构造 after_llm_request 事件参数。"""
    params: dict[str, Any] = {
        "request_name": _NDFC_REQUEST_NAME,
        "model_identifier": "test-model",
        "stream": False,
        "success": True,
        "message": _REFUSAL,
        "reasoning_content": None,
        "reasoning_parts": [],
        "tool_calls": list(_CALLS),
        "meta_data": {} if stream_id is None else {"stream_id": stream_id},
    }
    params.update(overrides)
    return params


def _before_params(
    payloads: list[Any], stream_id: str | None = _STREAM_A, **overrides: Any
) -> dict[str, Any]:
    """构造 before_llm_request 事件参数。"""
    params: dict[str, Any] = {
        "request_name": _NDFC_REQUEST_NAME,
        "model_identifier": "test-model",
        "stream": False,
        "tools": [],
        "payloads": payloads,
        "meta_data": {} if stream_id is None else {"stream_id": stream_id},
    }
    params.update(overrides)
    return params


class _ResumeSpy:
    """记录 resume 调用的替身。"""

    def __init__(self, result: bool = True) -> None:
        """构造替身。"""
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, stream_id: str, *, source: str, resume_prompt: str
    ) -> bool:
        """记录调用参数并返回预设结果。"""
        self.calls.append(
            {
                "stream_id": stream_id,
                "source": source,
                "resume_prompt": resume_prompt,
            }
        )
        return self.result


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    """每个用例前后都清空运行时状态与共享的隔离缓冲。

    重试状态按流隔离但模块级共享，隔离缓冲则是进程级容量固定的环形缓冲：
    用例产生的拦截记录若不清空，会把缓冲塞满并干扰其它用例对新增记录的断言。
    """
    quarantine._store = None
    reset_all_retry_states()
    yield
    reset_all_retry_states()
    quarantine._store = None


def _user(text: str) -> LLMPayload:
    """构造 USER payload。"""
    return LLMPayload(ROLE.USER, [Text(text)])


def _marker_payload() -> LLMPayload:
    """构造一条模拟 NDFC 内存链中残留的内部恢复 payload。"""
    return LLMPayload(ROLE.USER, [Text(NDFC_RESUME_PROMPT)])


def _texts(payloads: list[Any]) -> str:
    """拼接一组 payload 的文本内容。"""
    chunks: list[str] = []
    for payload in payloads:
        content = getattr(payload, "content", None)
        parts = content if isinstance(content, list) else [content]
        chunks.extend(
            part.text for part in parts if isinstance(getattr(part, "text", None), str)
        )
    return "\n".join(chunks)


def _patch_resume(
    monkeypatch: pytest.MonkeyPatch, spy: _ResumeSpy
) -> _ResumeSpy:
    """把 after 处理器使用的恢复入口替换为替身。"""
    from plugins.response_guard.handlers import after_llm_request as module

    monkeypatch.setattr(module, "resume_chatter_for_guard", spy)
    return spy


# ── 拦截后自动恢复 ──────────────────────────────────────────────────────


async def test_blocked_response_requests_ndfc_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中拦截后清空响应并按 stream 请求恢复。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params()

    decision, returned = await _after_handler().execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.SUCCESS
    assert returned["message"] is None
    assert returned["tool_calls"] == []
    assert len(spy.calls) == 1
    assert spy.calls[0]["stream_id"] == _STREAM_A
    assert spy.calls[0]["source"] == NDFC_RETRY_SOURCE
    assert spy.calls[0]["resume_prompt"] == NDFC_RESUME_PROMPT
    assert NDFC_RETRY_MARKER in spy.calls[0]["resume_prompt"]


async def test_passing_response_does_not_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守卫放行时不触发恢复，也不改动响应。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params(message="好呀，那我们继续。", tool_calls=[])

    decision, returned = await _after_handler().execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["message"] == "好呀，那我们继续。"
    assert spy.calls == []


async def test_other_chatter_does_not_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KFC 等其它 chatter 不进入 NDFC 重试逻辑。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params(request_name="kokoro_flow_chatter")

    decision, returned = await _after_handler().execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL
    assert spy.calls == []


async def test_stream_response_does_not_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流式请求跳过检测与恢复。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params(stream=True)

    decision, returned = await _after_handler().execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["message"] == _REFUSAL
    assert spy.calls == []


async def test_missing_stream_id_blocks_without_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 stream_id 时仍然拦截，但不尝试恢复。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params(stream_id=None)

    decision, returned = await _after_handler().execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.SUCCESS
    assert returned["message"] is None
    assert returned["tool_calls"] == []
    assert spy.calls == []


# ── 预算与降级 ──────────────────────────────────────────────────────────


async def test_retry_budget_is_enforced(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """预算耗尽后不再恢复，但拦截依然成立。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    handler = _after_handler(ndfc_max_retries=3)

    for expected in (1, 2, 3):
        params = _after_params()
        decision, _ = await handler.execute("after_llm_request", params)
        assert decision is EventDecision.SUCCESS
        assert params["message"] is None
    assert len(spy.calls) == 3

    # 第四次命中：预算已满，只拦截
    params = _after_params()
    decision, returned = await handler.execute("after_llm_request", params)
    assert decision is EventDecision.SUCCESS
    assert returned["message"] is None
    assert returned["tool_calls"] == []
    assert len(spy.calls) == 3


async def test_retry_disabled_by_zero_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上限设为 0 时只拦截不恢复。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params()

    decision, _ = await _after_handler(ndfc_max_retries=0).execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.SUCCESS
    assert params["message"] is None
    assert spy.calls == []


async def test_retry_disabled_by_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关关闭时只拦截不恢复。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _after_params()

    decision, _ = await _after_handler(ndfc_retry_enabled=False).execute(
        "after_llm_request", params
    )

    assert decision is EventDecision.SUCCESS
    assert params["message"] is None
    assert spy.calls == []


async def test_resume_failure_keeps_block_without_spending_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复未生效时不消耗预算、不留下待注入提醒，拦截照旧。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy(result=False))
    handler = _after_handler(ndfc_max_retries=3)

    # 三次全部恢复失败：每次都是新的尝试，而不是一次性耗尽
    for _ in range(4):
        params = _after_params()
        decision, returned = await handler.execute("after_llm_request", params)
        assert decision is EventDecision.SUCCESS
        assert returned["message"] is None
    assert len(spy.calls) == 4

    # 没有排入任何恢复，因此不会有待注入提醒
    before = await _before_handler().execute(
        "before_llm_request", _before_params([_user("正常消息")])
    )
    assert before[0] is EventDecision.PASS
    assert NDFC_RETRY_REMINDER not in _texts(before[1]["payloads"])


# ── 恢复标记的过滤与一次性提醒 ──────────────────────────────────────────


async def test_retry_marker_is_filtered() -> None:
    """携带内部标记的历史 payload 不会进入发送视图。"""
    payloads = [_user("正常消息"), _marker_payload()]
    params = _before_params(payloads)

    decision, returned = await _before_handler().execute(
        "before_llm_request", params
    )

    assert decision is EventDecision.SUCCESS
    assert len(returned["payloads"]) == 1
    assert NDFC_RETRY_MARKER not in _texts(returned["payloads"])
    # 只替换发送视图：调用方传入的原列表不被就地修改
    assert len(payloads) == 2


async def test_marker_is_filtered_on_every_later_request() -> None:
    """内存链里的标记会被长期反复剔除，不只在重试那一轮生效。"""
    payloads = [_user("正常消息"), _marker_payload()]

    for _ in range(3):
        decision, returned = await _before_handler().execute(
            "before_llm_request", _before_params(payloads)
        )
        assert decision is EventDecision.SUCCESS
        assert len(returned["payloads"]) == 1
        assert NDFC_RETRY_MARKER not in _texts(returned["payloads"])


async def test_clean_request_is_left_untouched() -> None:
    """没有标记、也没有待注入提醒时保持 PASS，不改动事件参数。"""
    payloads = [_user("正常消息"), _user("另一条消息")]
    params = _before_params(payloads)

    decision, returned = await _before_handler().execute(
        "before_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["payloads"] is payloads


async def test_pending_retry_injects_transient_reminder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拦截至恢复之间的那次请求会被临时追加一条提醒。"""
    _patch_resume(monkeypatch, _ResumeSpy())
    await _after_handler().execute("after_llm_request", _after_params())

    payloads = [_user("正常消息"), _marker_payload()]
    decision, returned = await _before_handler().execute(
        "before_llm_request", _before_params(payloads)
    )

    assert decision is EventDecision.SUCCESS
    assert len(returned["payloads"]) == 2
    assert NDFC_RETRY_REMINDER in _texts(returned["payloads"])
    assert NDFC_RETRY_MARKER not in _texts(returned["payloads"])
    # 提醒只存在于发送视图，原始 payload 列表不含提醒
    assert NDFC_RETRY_REMINDER not in _texts(payloads)

    # 一次性：紧接着的下一次请求不再带提醒
    _, again = await _before_handler().execute(
        "before_llm_request", _before_params(payloads)
    )
    assert NDFC_RETRY_REMINDER not in _texts(again["payloads"])


async def test_reminders_never_accumulate_across_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续三次重试中，任何一次请求最多只带一条提醒。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()
    payloads = [_user("正常消息"), _marker_payload()]

    for round_index in range(1, 4):
        await after.execute("after_llm_request", _after_params())
        assert len(spy.calls) == round_index

        decision, returned = await before.execute(
            "before_llm_request", _before_params(payloads)
        )
        assert decision is EventDecision.SUCCESS
        text = _texts(returned["payloads"])
        assert text.count(NDFC_RETRY_REMINDER) == 1
        assert len(returned["payloads"]) == 2

    # 预算耗尽后再无提醒
    await after.execute("after_llm_request", _after_params())
    _, last = await before.execute(
        "before_llm_request", _before_params(payloads)
    )
    assert NDFC_RETRY_REMINDER not in _texts(last["payloads"])


async def test_recovered_chain_clears_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复链中途守卫放行即收束，不会继续消耗预算。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()
    payloads = [_user("正常消息"), _marker_payload()]

    for _ in range(2):
        await after.execute("after_llm_request", _after_params())
        await before.execute("before_llm_request", _before_params(payloads))
    assert len(spy.calls) == 2

    # 第三次响应正常：状态清空
    await after.execute(
        "after_llm_request", _after_params(message="恢复后的正常回复", tool_calls=[])
    )

    # 新一轮命中的是新的初始请求，重新拥有完整预算
    for expected in (1, 2, 3):
        await after.execute("after_llm_request", _after_params())
        await before.execute("before_llm_request", _before_params(payloads))
        assert len(spy.calls) == 2 + expected


async def test_new_normal_request_resets_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算耗尽后，下一轮正常请求重新拥有完整预算。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()
    payloads = [_user("某用户的新消息"), _marker_payload()]

    for _ in range(4):
        await after.execute("after_llm_request", _after_params())
        await before.execute("before_llm_request", _before_params(payloads))
    assert len(spy.calls) == 3

    # 新消息带来的普通请求（没有待注入提醒）会重置该流的预算
    await before.execute("before_llm_request", _before_params(payloads))
    for expected in (1, 2, 3):
        await after.execute("after_llm_request", _after_params())
        await before.execute("before_llm_request", _before_params(payloads))
        assert len(spy.calls) == 3 + expected


async def test_streams_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同聊天流的预算与提醒互不影响。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()

    # A 连吃两次拦截
    for _ in range(2):
        await after.execute("after_llm_request", _after_params(_STREAM_A))
    assert [call["stream_id"] for call in spy.calls] == [_STREAM_A, _STREAM_A]

    # B 的第一次拦截不受 A 的预算影响
    await after.execute("after_llm_request", _after_params(_STREAM_B))
    assert [call["stream_id"] for call in spy.calls] == [
        _STREAM_A,
        _STREAM_A,
        _STREAM_B,
    ]

    # 提醒只注入到真正待恢复的那条流
    payloads = [_user("消息"), _marker_payload()]
    _, b_view = await before.execute(
        "before_llm_request", _before_params(payloads, _STREAM_B)
    )
    assert NDFC_RETRY_REMINDER in _texts(b_view["payloads"])

    _, a_other = await before.execute(
        "before_llm_request", _before_params(payloads, "stream-ccc")
    )
    assert NDFC_RETRY_REMINDER not in _texts(a_other["payloads"])

    # A 仍有自己的待注入提醒，未被 B 抢走
    _, a_view = await before.execute(
        "before_llm_request", _before_params(payloads, _STREAM_A)
    )
    assert NDFC_RETRY_REMINDER in _texts(a_view["payloads"])


async def test_multi_model_fallback_keeps_single_reminder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一次请求的后续模型候选不会重复注入提醒，也不会重置预算。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()
    payloads = [_user("消息"), _marker_payload()]

    await after.execute("after_llm_request", _after_params())

    decisions = []
    for _ in range(3):
        decision, returned = await before.execute(
            "before_llm_request", _before_params(payloads)
        )
        decisions.append((decision, _texts(returned["payloads"])))
    assert decisions[0][0] is EventDecision.SUCCESS
    assert decisions[0][1].count(NDFC_RETRY_REMINDER) == 1
    assert decisions[1][0] is EventDecision.SUCCESS
    assert NDFC_RETRY_REMINDER not in decisions[1][1]

    # 预算没有被候选重复触发重置：仍然只剩 2 次
    for _ in range(2):
        await after.execute("after_llm_request", _after_params())
    await after.execute("after_llm_request", _after_params())
    assert len(spy.calls) == 3


# ── 非公开框架入口的封装 ────────────────────────────────────────────────


async def test_resume_wrapper_skips_empty_stream_id() -> None:
    """缺少 stream_id 时不触碰框架，直接报告失败。"""
    assert (
        await framework_compat.resume_chatter_for_guard(
            "", source=NDFC_RETRY_SOURCE, resume_prompt=NDFC_RESUME_PROMPT
        )
        is False
    )


async def test_resume_wrapper_tolerates_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """框架不提供该入口时返回 False，不向外抛异常。"""

    def _boom() -> Any:
        raise ImportError("no chatter manager")

    monkeypatch.setattr(framework_compat, "_load_resume_entry", _boom)
    assert (
        await framework_compat.resume_chatter_for_guard(
            _STREAM_A, source=NDFC_RETRY_SOURCE, resume_prompt=NDFC_RESUME_PROMPT
        )
        is False
    )


async def test_resume_wrapper_tolerates_call_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复调用抛异常时返回 False，不向外抛异常。"""

    class _Manager:
        async def resume_chatter(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("stream loop manager down")

    monkeypatch.setattr(
        framework_compat, "_load_resume_entry", lambda: _Manager
    )
    assert (
        await framework_compat.resume_chatter_for_guard(
            _STREAM_A, source=NDFC_RETRY_SOURCE, resume_prompt=NDFC_RESUME_PROMPT
        )
        is False
    )


async def test_resume_wrapper_passes_source_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复调用携带约定的 source 与提示文本。"""
    seen: dict[str, Any] = {}

    class _Manager:
        async def resume_chatter(
            self,
            stream_id: str,
            source: str,
            *,
            extra: dict[str, Any] | None = None,
        ) -> bool:
            seen.update(
                {"stream_id": stream_id, "source": source, "extra": extra}
            )
            return True

    monkeypatch.setattr(
        framework_compat, "_load_resume_entry", lambda: _Manager
    )
    assert (
        await framework_compat.resume_chatter_for_guard(
            _STREAM_A, source=NDFC_RETRY_SOURCE, resume_prompt=NDFC_RESUME_PROMPT
        )
        is True
    )
    assert seen["stream_id"] == _STREAM_A
    assert seen["source"] == NDFC_RETRY_SOURCE
    assert seen["extra"] == {"resume_prompt": NDFC_RESUME_PROMPT}


# ── 与 KFC 路径的隔离 ──────────────────────────────────────────────────


async def test_before_handler_ignores_other_chatter() -> None:
    """KFC 的请求不经过 NDFC 的标记过滤与提醒注入。"""
    payloads = [_user("消息"), _marker_payload()]
    params = _before_params(payloads, request_name="kokoro_flow_chatter")

    decision, returned = await _before_handler().execute(
        "before_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["payloads"] is payloads


async def test_before_handler_ignores_stream_request() -> None:
    """流式请求跳过处理。"""
    payloads = [_user("消息"), _marker_payload()]
    params = _before_params(payloads, stream=True)

    decision, returned = await _before_handler().execute(
        "before_llm_request", params
    )

    assert decision is EventDecision.PASS
    assert returned["payloads"] is payloads


async def test_handlers_disabled_by_config() -> None:
    """关闭事件接入后两个处理器都不介入。"""
    assert (
        await _before_handler(guard_ndfc=False).execute(
            "before_llm_request", _before_params([_marker_payload()])
        )
    )[0] is EventDecision.PASS
    assert (
        await _before_handler(enabled=False).execute(
            "before_llm_request", _before_params([_marker_payload()])
        )
    )[0] is EventDecision.PASS
    assert (
        await _after_handler(guard_ndfc=False).execute(
            "after_llm_request", _after_params()
        )
    )[0] is EventDecision.PASS


async def test_missing_meta_data_is_tolerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件参数缺少 meta_data 时按放行处理，不抛异常。"""
    spy = _patch_resume(monkeypatch, _ResumeSpy())
    params = _before_params([_marker_payload()])
    params.pop("meta_data")

    decision, returned = await _before_handler().execute(
        "before_llm_request", params
    )

    # 仍会剔除标记，但没有 stream_id 就不会注入提醒
    assert decision is EventDecision.SUCCESS
    assert returned["payloads"] == []
    assert spy.calls == []


# ── 真实事件总线上的回写协议 ────────────────────────────────────────────


async def test_event_bus_round_trip_writes_back_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在真实 EventBus 上验证两个处理器的回写与协议兼容性。

    处理器必须返回 key 集合与入参一致的新参数，否则事件总线会丢弃本次改动。
    这里跑完整的发布链路：拦截 → 恢复 → 过滤标记 → 注入一次性提醒。
    """
    from src.core.components.types import EventType
    from src.kernel.event import EventBus

    spy = _patch_resume(monkeypatch, _ResumeSpy())
    after = _after_handler(ndfc_max_retries=3)
    before = _before_handler()

    bus = EventBus("response-guard-test")
    bus.subscribe(
        EventType.AFTER_LLM_REQUEST, after.execute, priority=after.weight
    )
    bus.subscribe(
        EventType.BEFORE_LLM_REQUEST, before.execute, priority=before.weight
    )

    decision, blocked = await bus.publish(
        EventType.AFTER_LLM_REQUEST, _after_params()
    )
    assert decision is EventDecision.SUCCESS
    assert blocked["message"] is None
    assert blocked["tool_calls"] == []
    assert len(spy.calls) == 1

    payloads = [_user("正常消息"), _marker_payload()]
    decision, send_params = await bus.publish(
        EventType.BEFORE_LLM_REQUEST, _before_params(payloads)
    )
    assert decision is EventDecision.SUCCESS
    assert len(send_params["payloads"]) == 2
    sent_text = _texts(send_params["payloads"])
    assert NDFC_RETRY_REMINDER in sent_text
    assert NDFC_RETRY_MARKER not in sent_text
    # 发送视图是新建列表，NDFC 侧原始 payload 列表不被就地修改
    assert send_params["payloads"] is not payloads
    assert len(payloads) == 2

    # 同一次请求的后续模型候选不再重复注入
    decision, again = await bus.publish(
        EventType.BEFORE_LLM_REQUEST, _before_params(payloads)
    )
    assert decision is EventDecision.SUCCESS
    assert len(again["payloads"]) == 1
    assert NDFC_RETRY_REMINDER not in _texts(again["payloads"])


# ── 插件装配 ────────────────────────────────────────────────────────────


def test_plugin_registers_both_event_handlers() -> None:
    """开启 NDFC 接入时同时注册拦截与请求预处理两个处理器。"""
    from plugins.response_guard.plugin import ResponseGuardPlugin

    enabled = ResponseGuardPlugin(_config())
    names = {component.name for component in enabled.get_components()}
    assert "response_guard_after_llm_request" in names
    assert "response_guard_before_llm_request" in names

    without_ndfc = ResponseGuardPlugin(_config(guard_ndfc=False))
    assert {
        component.name for component in without_ndfc.get_components()
    } == {"response_guard"}

    disabled = ResponseGuardPlugin(_config(enabled=False))
    assert disabled.get_components() == []


async def test_plugin_unload_clears_retry_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """插件卸载会清空运行时重试状态，重载后不会残留旧预算与提醒。"""
    from plugins.response_guard.plugin import ResponseGuardPlugin

    _patch_resume(monkeypatch, _ResumeSpy())
    await _after_handler().execute("after_llm_request", _after_params())

    plugin = ResponseGuardPlugin(_config())
    await plugin.on_plugin_unloaded()

    _, returned = await _before_handler().execute(
        "before_llm_request", _before_params([_user("消息"), _marker_payload()])
    )
    assert NDFC_RETRY_REMINDER not in _texts(returned["payloads"])
