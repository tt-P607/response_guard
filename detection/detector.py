"""响应字段的文本抽取与判定汇总。

把一轮响应的各字段拆成互不拼接的待检测文本，逐段检测后汇总为最终判定。
逐段检测而非整体拼接，是为了避免不同段落的词产生虚假共现。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from ..verdict import GuardVerdict, PASS_VERDICT, Verdict
from .patterns import TOOL_TEXT_KEYS
from .reasoning import inspect_reasoning
from .reply import inspect_reply

__all__ = ["inspect_response", "join_reasoning"]


def inspect_response(
    *,
    reply_text: str | None = None,
    reasoning_text: str | None = None,
    reasoning_parts: Sequence[Any] = (),
    tool_calls: Sequence[Any] = (),
    check_reasoning: bool = True,
) -> GuardVerdict:
    """检测一轮响应中的推理链与全部对外可见正文。

    Args:
        reply_text: 响应正文（``message``）。
        reasoning_text: 推理正文（``reasoning_content``）。
        reasoning_parts: 推理分段（``reasoning_parts``）。
        tool_calls: 本轮工具调用；其中的可见正文参数一并检测。
        check_reasoning: 是否检测推理链。

    Returns:
        GuardVerdict: 任一段文本命中返回 MODEL_REFUSAL；否则返回结构近似的
        UNCERTAIN；都没有则 PASS。
    """
    blocked: list[str] = []
    uncertain: list[str] = []

    if check_reasoning:
        reasoning_blob = join_reasoning(reasoning_text, reasoning_parts)
        if reasoning_blob:
            _collect(inspect_reasoning(reasoning_blob), blocked, uncertain)

    for text in _iter_visible_texts(reply_text, tool_calls):
        _collect(inspect_reply(text), blocked, uncertain)

    if blocked:
        return GuardVerdict(Verdict.MODEL_REFUSAL, _dedupe(blocked))
    if uncertain:
        return GuardVerdict(Verdict.UNCERTAIN, _dedupe(uncertain))
    return PASS_VERDICT


def _collect(
    verdict: GuardVerdict, blocked: list[str], uncertain: list[str]
) -> None:
    """把单段文本的判定结果归入对应证据列表。"""
    if verdict.blocks:
        blocked.extend(verdict.evidence)
    elif verdict.is_uncertain:
        uncertain.extend(verdict.evidence)


def _dedupe(items: list[str]) -> tuple[str, ...]:
    """按出现顺序去重。"""
    return tuple(dict.fromkeys(items))


def join_reasoning(
    reasoning_text: str | None, reasoning_parts: Sequence[Any]
) -> str:
    """合并推理正文与推理分段。

    框架会用同一份内容同时填充两个字段，因此优先使用分段，避免重复。

    Args:
        reasoning_text: 推理正文（``reasoning_content``）。
        reasoning_parts: 推理分段（``reasoning_parts``）。

    Returns:
        str: 合并后的推理文本；两处都为空时返回空字符串。
    """
    part_texts = [
        text
        for part in reasoning_parts
        if isinstance((text := getattr(part, "text", None)), str) and text.strip()
    ]
    if part_texts:
        return "\n".join(part_texts)
    return reasoning_text if isinstance(reasoning_text, str) else ""


def _iter_visible_texts(
    reply_text: str | None, tool_calls: Sequence[Any]
) -> Iterator[str]:
    """产出所有「实际会被用户看到」的文本片段。"""
    if isinstance(reply_text, str) and reply_text.strip():
        yield reply_text
    for call in tool_calls:
        args = getattr(call, "args", None)
        if isinstance(args, Mapping):
            yield from _iter_visible_arg_texts(args)


def _iter_visible_arg_texts(args: Mapping[Any, Any]) -> Iterator[str]:
    """产出工具调用参数中属于对外正文的字符串。"""
    for key, value in args.items():
        if str(key).lower() in TOOL_TEXT_KEYS:
            yield from _iter_strings(value)


def _iter_strings(value: Any) -> Iterator[str]:
    """递归产出值中的非空字符串。"""
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)
