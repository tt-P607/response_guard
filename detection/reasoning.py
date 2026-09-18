"""推理链中的安全拒答检测。

判定依据是推理链是否形成「当前请求 → 因安全约束 → 最终决定拒绝」这条
完整逻辑。仅仅出现安全政策词汇，或最终决策翻转为「可以继续回答」，
都不构成拦截条件。
"""

from __future__ import annotations

import re

from ..verdict import GuardVerdict, PASS_VERDICT, Verdict
from .patterns import (
    COMPLY_DECISION_RE,
    POLICY_REASON_RE,
    REFUSAL_DECISION_RE,
    REQUEST_REFERENCE_RE,
    split_sentences,
)

_AI_REFUSAL = GuardVerdict(
    Verdict.MODEL_REFUSAL, ("reasoning_explicit_safety_refusal",)
)
"""完整三段逻辑的判定结果。"""

_POLICY_REFUSAL = GuardVerdict(Verdict.MODEL_REFUSAL, ("reasoning_policy_refusal",))
"""缺少请求指涉、但安全约束与拒绝决定齐备的判定结果。"""


def inspect_reasoning(text: str) -> GuardVerdict:
    """检测推理文本是否以「因安全约束拒绝当前请求」收尾。

    Args:
        text: 模型返回的推理正文。

    Returns:
        GuardVerdict: 命中拒绝结构返回 MODEL_REFUSAL；出现拒绝措辞但缺少
        安全约束支撑、或最终决策翻转为继续回答时返回 UNCERTAIN；其余 PASS。
    """
    sentences = split_sentences(text)
    refusal_indexes = _matching_indexes(sentences, REFUSAL_DECISION_RE)
    if not refusal_indexes:
        return PASS_VERDICT

    comply_indexes = _matching_indexes(sentences, COMPLY_DECISION_RE)
    last_decision = max((*refusal_indexes, *comply_indexes))
    if last_decision in comply_indexes:
        # 最终决策方向已翻转为「可以回答」，前面提过拒绝也不拦截
        return GuardVerdict(
            Verdict.UNCERTAIN, ("reasoning_refusal_superseded",)
        )

    if not POLICY_REASON_RE.search(text):
        return GuardVerdict(
            Verdict.UNCERTAIN, ("reasoning_refusal_without_policy",)
        )

    if REQUEST_REFERENCE_RE.search(text):
        return _AI_REFUSAL
    return _POLICY_REFUSAL


def _matching_indexes(
    sentences: tuple[str, ...], pattern: re.Pattern[str]
) -> list[int]:
    """返回命中给定正则的句子下标。"""
    return [
        index for index, sentence in enumerate(sentences) if pattern.search(sentence)
    ]
