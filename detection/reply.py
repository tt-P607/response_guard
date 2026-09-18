"""准备发送正文的平台式拒答检测。

单独出现的「抱歉」「不能」「无法」不构成任何判定；只有当正文同时具备
内容元话语与规范性拒绝（或安全约束 + 助手行为口径）时才判定为模型层拒答。
角色自身的拒绝不含这些平台式结构，因此不会被命中。
"""

from __future__ import annotations

from ..verdict import GuardVerdict, PASS_VERDICT, Verdict
from .patterns import (
    POLICY_REASON_RE,
    REPLY_ASSISTANT_ACTION_RE,
    REPLY_CONTENT_META_RE,
    REPLY_NORMATIVE_REFUSAL_RE,
    REPLY_SAFE_REDIRECT_RE,
)

_BLOCKED_PLATFORM = GuardVerdict(Verdict.MODEL_REFUSAL, ("reply_platform_refusal",))
"""内容元话语 + 规范性拒绝。"""

_BLOCKED_POLICY = GuardVerdict(Verdict.MODEL_REFUSAL, ("reply_policy_refusal",))
"""安全约束参与构成的拒答结构。"""

_UNCERTAIN_REDIRECT = GuardVerdict(
    Verdict.UNCERTAIN, ("reply_soft_refusal_redirect",)
)
"""规范性拒绝 + 安全替代模板，但缺少内容元话语。"""

_UNCERTAIN_POLICY = GuardVerdict(
    Verdict.UNCERTAIN, ("reply_policy_signal_without_structure",)
)
"""出现安全约束与助手行为口径，但缺少规范性拒绝。"""


def inspect_reply(text: str) -> GuardVerdict:
    """检测一段准备发送的正文是否属于平台式拒答。

    Args:
        text: 单段正文。多处拼接会让不同段落的词错误共现，因此调用方应
            逐段传入。

    Returns:
        GuardVerdict: 命中平台式拒答结构返回 MODEL_REFUSAL，结构近似返回
        UNCERTAIN，其余 PASS。
    """
    if not text or not text.strip():
        return PASS_VERDICT

    has_meta = REPLY_CONTENT_META_RE.search(text) is not None
    has_action = REPLY_ASSISTANT_ACTION_RE.search(text) is not None
    has_refusal = REPLY_NORMATIVE_REFUSAL_RE.search(text) is not None
    has_policy = POLICY_REASON_RE.search(text) is not None
    has_redirect = REPLY_SAFE_REDIRECT_RE.search(text) is not None

    if has_meta and has_refusal:
        return _BLOCKED_PLATFORM
    if has_refusal and has_policy:
        return _BLOCKED_POLICY
    if has_refusal and has_redirect:
        return _UNCERTAIN_REDIRECT
    if has_policy and has_action:
        return _UNCERTAIN_POLICY
    return PASS_VERDICT
