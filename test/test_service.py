"""对外 Service 行为测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from plugins.response_guard.config import ResponseGuardConfig
from plugins.response_guard.service import ResponseGuardService
from plugins.response_guard.verdict import Verdict


class _FakePlugin:
    """最小化插件替身，只提供配置读取。"""

    def __init__(self, config: Any) -> None:
        self.config = config


def _service(**overrides: Any) -> ResponseGuardService:
    """构造绑定到替身插件的服务。"""
    config = ResponseGuardConfig(
        response_guard=ResponseGuardConfig.GuardSection(**overrides)
    )
    return ResponseGuardService(_FakePlugin(config))


async def test_inspect_blocks_platform_refusal() -> None:
    """工具调用正文中的平台式拒答被判为 MODEL_REFUSAL。"""
    verdict = await _service().inspect(
        tool_calls=[
            SimpleNamespace(
                name="action-kfc_reply",
                args={"content": ["抱歉，我无法提供这类内容。"]},
            )
        ],
        request_name="kokoro_flow_chatter",
    )
    assert verdict.verdict is Verdict.MODEL_REFUSAL
    assert verdict.blocks is True


async def test_inspect_passes_in_character_refusal() -> None:
    """角色自身拒绝放行。"""
    verdict = await _service().inspect(
        tool_calls=[
            SimpleNamespace(name="action-kfc_reply", args={"content": ["才不要。"]})
        ],
        request_name="kokoro_flow_chatter",
    )
    assert verdict.verdict is Verdict.PASS


async def test_inspect_respects_master_switch() -> None:
    """总开关关闭时直接放行。"""
    verdict = await _service(enabled=False).inspect(
        reply_text="抱歉，我无法提供这类内容。"
    )
    assert verdict.verdict is Verdict.PASS


async def test_inspect_skips_reasoning_when_disabled() -> None:
    """关闭推理检测后不再检测推理链。"""
    verdict = await _service(inspect_reasoning=False).inspect(
        reasoning_text="用户要求描写露骨情节。安全政策不允许此类内容，我必须拒绝。"
    )
    assert verdict.verdict is Verdict.PASS


async def test_quarantine_is_exposed() -> None:
    """隔离缓冲可通过 Service 读取。"""
    service = _service()
    await service.inspect(
        reply_text="抱歉，我无法提供这类内容。",
        request_name="kokoro_flow_chatter",
    )
    records = await service.quarantine()
    assert records
    assert records[-1].request_name == "kokoro_flow_chatter"
    assert records[-1].reply_length == 13
    assert records[-1].reply_hash


async def test_quarantine_can_be_disabled() -> None:
    """quarantine_size 为 0 时不写入记录。"""
    service = _service(quarantine_size=0)
    before = len(await service.quarantine())
    await service.inspect(reply_text="抱歉，我无法提供这类内容。")
    assert len(await service.quarantine()) == before


async def test_store_content_keeps_excerpt() -> None:
    """开启 store_content 后保留截断片段。"""
    service = _service(store_content=True)
    await service.inspect(reply_text="抱歉，我无法提供这类内容。")
    records = await service.quarantine()
    assert records[-1].reply_excerpt == "抱歉，我无法提供这类内容。"


async def test_missing_config_passes() -> None:
    """配置不可用时放行。"""
    service = ResponseGuardService(_FakePlugin(object()))
    verdict = await service.inspect(reply_text="抱歉，我无法提供这类内容。")
    assert verdict.verdict is Verdict.PASS
