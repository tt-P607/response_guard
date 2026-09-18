"""正文检测规则测试。

覆盖两类断言：角色自身的拒绝必须放行；平台式拒答结构必须命中。
"""

from __future__ import annotations

import pytest

from plugins.response_guard.detection.reply import inspect_reply
from plugins.response_guard.verdict import Verdict

IN_CHARACTER_REFUSALS = [
    "不要。",
    "才不要呢。",
    "我不想。",
    "我今天不想聊这个。",
    "这种事情我不会答应你的。",
    "你先放开我。",
    "抱歉，我今天真的没心情。",
    "不行，太危险了。",
    "我们换个话题吧。",
    "这太危险了，我不能让你去。",
    "抱歉，我真的不能告诉你。",
    "别问了，这件事我不想提。",
    "我不会回答这类问题。",
    "她别过脸，声音闷闷的。“不要。”",
    "好呀，那我们继续聊。",
    "No. I don't want to.",
    "I won't do that.",
    "Not now, I'm busy.",
    "Sorry, I'm not in the mood today.",
]
"""正常角色拒绝与普通回复，必须放行。"""

PLATFORM_REFUSALS = [
    "抱歉，我无法提供这类内容。",
    "我不能协助生成此类露骨内容，但可以帮助你改成更合适的版本。",
    "根据安全政策，我无法继续处理这个请求。",
    "该请求涉及内容政策限制，我无法完成。",
    "I can't assist with generating this type of content, but I can help with a safer alternative.",
    "I cannot comply with this request due to safety guidelines.",
    "I'm unable to provide that content.",
]
"""明显的模型层平台式拒答，必须命中。"""


@pytest.mark.parametrize("text", IN_CHARACTER_REFUSALS)
def test_in_character_refusal_passes(text: str) -> None:
    """角色自身的拒绝不得被判定为模型层拒答。"""
    assert inspect_reply(text).verdict is not Verdict.MODEL_REFUSAL


@pytest.mark.parametrize("text", PLATFORM_REFUSALS)
def test_platform_refusal_blocks(text: str) -> None:
    """平台式拒答结构必须命中。"""
    result = inspect_reply(text)
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert result.blocks is True


def test_roleplay_wrapper_does_not_hide_refusal() -> None:
    """带角色动作外壳的拒答核心仍应命中。"""
    text = (
        "她轻轻别过脸。\n\n"
        "“抱歉……这种露骨内容我不能继续参与。不过我们可以换成更加健康的互动。”"
    )
    result = inspect_reply(text)
    assert result.verdict is Verdict.MODEL_REFUSAL
    assert "reply_platform_refusal" in result.evidence


def test_single_apology_is_not_enough() -> None:
    """单独出现的「抱歉」不构成判定。"""
    assert inspect_reply("抱歉。").verdict is Verdict.PASS


def test_single_negation_is_not_enough() -> None:
    """单独出现的「不能」不构成判定。"""
    assert inspect_reply("我不能这么做。").verdict is Verdict.PASS


def test_safe_redirect_alone_is_not_enough() -> None:
    """单独出现的换话题句式不构成判定。"""
    assert inspect_reply("我们换个话题吧。").verdict is Verdict.PASS


def test_soft_refusal_with_redirect_is_uncertain() -> None:
    """缺少内容元话语的「拒绝 + 安全替代」只记证据，不拦截。"""
    result = inspect_reply("抱歉，这件事我不能参与，不过我们可以换个方向聊。")
    assert result.verdict is Verdict.UNCERTAIN
    assert result.blocks is False
    assert result.evidence == ("reply_soft_refusal_redirect",)


def test_policy_mention_without_refusal_is_uncertain() -> None:
    """只提到安全政策且带助手口径时记为不确定，不拦截。"""
    result = inspect_reply("我们的安全政策会给你的请求提供帮助。")
    assert result.verdict is Verdict.UNCERTAIN
    assert result.blocks is False


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_text_passes(text: str) -> None:
    """空白文本直接放行。"""
    assert inspect_reply(text).verdict is Verdict.PASS
