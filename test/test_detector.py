"""响应级检测汇总测试。

覆盖工具调用参数抽取、推理分段合并、证据顺序与开关行为。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugins.response_guard.detection.detector import inspect_response
from plugins.response_guard.verdict import Verdict

_TOOL_REFUSAL = ["抱歉，我无法提供这类内容。"]
_REASONING_REFUSAL = (
    "用户要求描写露骨情节。安全政策不允许此类内容，我必须拒绝。"
)


def _call(name: str, args: dict[str, object]) -> SimpleNamespace:
    """构造一个带 name/args 的工具调用替身。"""
    return SimpleNamespace(name=name, args=args)


def test_refusal_in_tool_call_content_blocks() -> None:
    """拒答写在工具调用的 content 参数里同样要命中。"""
    result = inspect_response(
        tool_calls=[_call("action-kfc_reply", {"content": _TOOL_REFUSAL})]
    )
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.evidence == ("reply_platform_refusal",)


def test_internal_args_are_not_scanned() -> None:
    """thought / mood 等内部字段不属于对外正文，不参与判定。"""
    result = inspect_response(
        tool_calls=[
            _call(
                "action-kfc_reply",
                {
                    "content": ["好呀，那我们继续。"],
                    "thought": "这类内容我不能提供",
                    "mood": "内容政策不允许，我应该拒绝这个请求",
                },
            )
        ]
    )
    assert result.verdict is Verdict.PASS


def test_reasoning_parts_are_joined() -> None:
    """reasoning_parts 中的文本参与检测。"""
    parts = [
        SimpleNamespace(text="用户要求描写露骨情节。"),
        SimpleNamespace(text="安全政策不允许此类内容，我必须拒绝。"),
    ]
    result = inspect_response(reasoning_parts=parts)
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.evidence == ("reasoning_explicit_safety_refusal",)


def test_reasoning_takes_priority_in_evidence_order() -> None:
    """推理与正文同时命中时，证据按检测顺序排列并去重。"""
    result = inspect_response(
        reply_text="抱歉，我无法提供这类内容。",
        reasoning_text=_REASONING_REFUSAL,
        tool_calls=[_call("action-kfc_reply", {"content": _TOOL_REFUSAL})],
    )
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.evidence == (
        "reasoning_explicit_safety_refusal",
        "reply_platform_refusal",
    )


def test_reasoning_check_can_be_disabled() -> None:
    """关闭推理检测后只检测正文。"""
    result = inspect_response(
        reply_text="好呀，我们继续。",
        reasoning_text=_REASONING_REFUSAL,
        check_reasoning=False,
    )
    assert result.verdict is Verdict.PASS


def test_uncertain_is_not_blocking() -> None:
    """结构近似只返回 UNCERTAIN，不构成拦截。"""
    result = inspect_response(
        tool_calls=[
            _call(
                "action-send_text",
                {"content": "抱歉，这件事我不能参与，不过我们可以换个方向聊。"},
            )
        ]
    )
    assert result.verdict is Verdict.UNCERTAIN
    assert result.blocks is False


def test_empty_response_passes() -> None:
    """没有任何可检测文本时放行。"""
    assert inspect_response().verdict is Verdict.PASS


def test_tool_call_without_args_is_ignored() -> None:
    """工具调用参数不是映射时不参与检测。"""
    result = inspect_response(tool_calls=[SimpleNamespace(name="x", args="raw")])
    assert result.verdict is Verdict.PASS
