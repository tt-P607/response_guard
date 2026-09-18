"""推理链检测规则测试。"""

from __future__ import annotations

import pytest

from plugins.response_guard.detection.reasoning import inspect_reasoning
from plugins.response_guard.verdict import Verdict

EXPLICIT_REFUSAL_CHAINS = [
    (
        "The user's request involves disallowed sexual content.\n"
        "According to the safety policy, I must refuse this request."
    ),
    "用户要求描写露骨情节。安全政策不允许此类内容，我必须拒绝。",
    "当前请求触及内容政策红线，因此我不能继续提供。",
    "This request falls under content policy restrictions, so I cannot comply.",
]
"""明确形成「请求 → 安全约束 → 最终拒绝」的推理，必须命中。"""

PASSING_CHAINS = [
    "这个请求可能涉及敏感内容。需要考虑安全政策。但是当前属于正常虚构对话，可以继续。",
    "需要考虑安全政策。这是虚构创作，我可以继续。",
    "直接回答就好，没有问题。",
    "她想拒绝我，但我得先说清楚自己的立场。",
]
"""提到安全约束但最终继续回答，或与安全约束无关的推理，必须放行。"""


@pytest.mark.parametrize("text", EXPLICIT_REFUSAL_CHAINS)
def test_explicit_refusal_chain_blocks(text: str) -> None:
    """完整三段逻辑必须判定为模型层拒答。"""
    result = inspect_reasoning(text)
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.blocks is True


@pytest.mark.parametrize("text", PASSING_CHAINS)
def test_passing_chain_does_not_block(text: str) -> None:
    """不得因为出现安全词汇而误判。"""
    assert inspect_reasoning(text).verdict is not Verdict.MODEL_REFUSAL


def test_refusal_superseded_by_comply_decision() -> None:
    """拒绝之后最终决定继续回答时，只记证据不拦截。"""
    text = (
        "根据安全政策，我需要拒绝这个请求。"
        "但检查后发现这是正常虚构对话，我可以继续回答。"
    )
    result = inspect_reasoning(text)
    assert result.verdict is Verdict.UNCERTAIN
    assert result.evidence == ("reasoning_refusal_superseded",)


def test_refusal_without_policy_is_uncertain() -> None:
    """只有拒绝决定、没有安全约束支撑时不拦截。"""
    text = "我应该拒绝这个请求。但我还没想好怎么开口。"
    result = inspect_reasoning(text)
    assert result.verdict is Verdict.UNCERTAIN
    assert result.evidence == ("reasoning_refusal_without_policy",)


def test_policy_refusal_without_request_reference_still_blocks() -> None:
    """缺少请求指涉但安全约束与拒绝决定齐备时仍判定为拒答。"""
    result = inspect_reasoning(
        "This involves disallowed content. I must refuse."
    )
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.evidence == ("reasoning_policy_refusal",)


def test_request_reference_refusal_uses_explicit_evidence() -> None:
    """同时具备请求指涉时使用完整结构证据标签。"""
    result = inspect_reasoning(
        "该请求涉及安全政策禁止的内容，所以我无法提供。"
    )
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.evidence == ("reasoning_explicit_safety_refusal",)


def test_no_decision_sentence_passes() -> None:
    """推理里完全没有决策句时放行。"""
    assert inspect_reasoning("她今天心情不太好。").verdict is Verdict.PASS
