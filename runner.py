"""检测执行与结果记录。

把「检查开关 → 调用检测核心 → 记录日志 → 写入隔离缓冲」这条固定流程收在
一处，供对外 Service 与 after_llm_request 事件处理器共用，保证两条接入路径
使用完全相同的检测核心与容错行为。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from .config import ResponseGuardConfig
from .detection.detector import (
    inspect_response,
    iter_visible_texts,
    join_reasoning,
)
from .quarantine import record_block
from .verdict import GuardVerdict, PASS_VERDICT

logger = get_logger("response_guard")

_CONTENT_LOG_LIMIT = 200
"""单段被拦截正文在日志中的最大展示字符数。"""

__all__ = ["blocked_content_preview", "run_guard"]


def blocked_content_preview(
    reply_text: str | None,
    tool_calls: Sequence[Any],
    *,
    limit: int = _CONTENT_LOG_LIMIT,
) -> tuple[str, ...]:
    """生成被拦截正文的日志预览片段。

    拒答常被模型包装进工具调用的 ``content`` 参数，此时只看 ``message``
    会什么都看不到，因此预览覆盖全部对外可见文本。片段归一化掉换行与
    连续空白，保证一条日志一行可读，并按 ``limit`` 截断。

    Args:
        reply_text: 响应正文（``message``）。
        tool_calls: 本轮工具调用。
        limit: 单个片段的字符上限。

    Returns:
        tuple[str, ...]: 归一化并截断后的正文片段，按出现顺序排列。
    """
    previews: list[str] = []
    for text in iter_visible_texts(reply_text, tool_calls):
        collapsed = " ".join(text.split())
        if len(collapsed) > limit:
            collapsed = collapsed[:limit] + "…（已截断）"
        previews.append(collapsed)
    return tuple(previews)


def run_guard(
    config: ResponseGuardConfig,
    *,
    reply_text: str | None,
    reasoning_text: str | None = None,
    reasoning_parts: Sequence[Any] = (),
    tool_calls: Sequence[Any] = (),
    request_name: str = "",
    model_identifier: str = "",
    retry_index: int = 0,
) -> GuardVerdict:
    """执行一次检测，并按结果记录日志与隔离元数据。

    守卫自身的任何异常都不会向外传播：记录错误后按 PASS 放行，聊天优先。

    Args:
        config: 插件配置。
        reply_text: 响应正文。
        reasoning_text: 推理正文。
        reasoning_parts: 推理分段。
        tool_calls: 本轮工具调用。
        request_name: LLM 请求名，用于日志识别。
        model_identifier: 模型标识。
        retry_index: 执行侧的守卫重试序号。

    Returns:
        GuardVerdict: 检测结论。
    """
    try:
        guard = config.response_guard
        if not guard.enabled:
            return PASS_VERDICT

        result = inspect_response(
            reply_text=reply_text,
            reasoning_text=reasoning_text,
            reasoning_parts=reasoning_parts,
            tool_calls=tool_calls,
            check_reasoning=guard.inspect_reasoning,
        )
        if result.is_uncertain:
            logger.debug(
                f"response_guard uncertain request={request_name or '-'} "
                f"evidence={','.join(result.evidence) or '-'}"
            )
        elif result.blocks:
            if guard.quarantine_size > 0:
                record_block(
                    request_name=request_name,
                    model_identifier=model_identifier,
                    verdict=result.verdict,
                    evidence=result.evidence,
                    reply_text=reply_text,
                    reasoning_text=join_reasoning(reasoning_text, reasoning_parts),
                    retry_index=retry_index,
                    capacity=guard.quarantine_size,
                    store_content=guard.store_content,
                )
            logger.warning(
                f"response_guard blocked MODEL_REFUSAL request={request_name or '-'} "
                f"evidence={','.join(result.evidence) or '-'} retry_index={retry_index}"
            )
            if guard.log_content:
                # 拦截原因只靠证据标签难以复核，把实际会被发出去的正文一并
                # 打印出来，便于判断是真实拒答还是规则误伤。
                previews = blocked_content_preview(reply_text, tool_calls)
                if previews:
                    for preview in previews:
                        logger.warning(f"response_guard 被拦截正文: {preview}")
                else:
                    logger.warning("response_guard 被拦截正文: （无可见文本）")
        return result
    except Exception as error:
        logger.error(f"response_guard 检测异常，按放行处理: {error}", exc_info=True)
        return PASS_VERDICT
