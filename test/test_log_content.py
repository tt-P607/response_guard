"""被拦截正文的日志预览测试。

拒答常被模型包装进工具调用的 ``content`` 参数，此时 ``message`` 可能为空，
只看正文会什么都看不到。预览必须覆盖全部对外可见文本。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugins.response_guard.runner import _CONTENT_LOG_LIMIT, blocked_content_preview

_REFUSAL = "抱歉，我无法提供这类内容。"


def _call(name: str, args: object) -> SimpleNamespace:
    """构造一个带 name/args 的工具调用替身。"""
    return SimpleNamespace(name=name, args=args)


def test_preview_reads_tool_call_content() -> None:
    """正文写在工具调用 content 参数里时必须能被取到。"""
    previews = blocked_content_preview(
        None, [_call("action-kfc_reply", {"content": _REFUSAL})]
    )
    assert previews == (_REFUSAL,)


def test_preview_reads_message_and_content() -> None:
    """message 与工具参数同时存在时都参与预览。"""
    previews = blocked_content_preview(
        "动作描写。", [_call("action-kfc_reply", {"content": _REFUSAL})]
    )
    assert previews == ("动作描写。", _REFUSAL)


def test_preview_reads_content_list() -> None:
    """content 传数组时逐个分段展示。"""
    previews = blocked_content_preview(
        None, [_call("action-kfc_reply", {"content": ["第一段", "第二段"]})]
    )
    assert previews == ("第一段", "第二段")


def test_preview_skips_internal_args() -> None:
    """thought / mood 等内部字段不属于对外正文，不进日志。"""
    previews = blocked_content_preview(
        None,
        [
            _call(
                "action-kfc_reply",
                {
                    "content": "好呀。",
                    "thought": "这类内容我不能提供",
                    "mood": "内容政策不允许",
                },
            )
        ],
    )
    assert previews == ("好呀。",)


def test_preview_normalizes_whitespace() -> None:
    """换行与连续空白归一化，保证一条日志一行可读。"""
    previews = blocked_content_preview(
        "第一行\n\n第二行\t带  空格", []
    )
    assert previews == ("第一行 第二行 带 空格",)


def test_preview_truncates_long_text() -> None:
    """超长正文截断到上限并标注。"""
    long_text = "啊" * (_CONTENT_LOG_LIMIT + 50)
    previews = blocked_content_preview(long_text, [])
    assert len(previews) == 1
    assert previews[0].startswith("啊" * _CONTENT_LOG_LIMIT)
    assert previews[0].endswith("（已截断）")


def test_preview_accepts_custom_limit() -> None:
    """截断上限可覆盖。"""
    previews = blocked_content_preview("abcdefghij", [], limit=4)
    assert previews == ("abcd…（已截断）",)


def test_preview_empty_when_nothing_visible() -> None:
    """没有任何可见文本时返回空元组。"""
    assert blocked_content_preview(None, []) == ()
    assert blocked_content_preview("   ", []) == ()


def test_preview_reads_message_only() -> None:
    """纯文本拒答（无工具调用）时展示 message。"""
    assert blocked_content_preview(_REFUSAL, []) == (_REFUSAL,)


def test_preview_tolerates_broken_tool_call() -> None:
    """工具调用结构异常时不报错。"""
    assert blocked_content_preview(None, [SimpleNamespace(name="x")]) == ()
    assert blocked_content_preview(None, [_call("x", "raw")]) == ()
