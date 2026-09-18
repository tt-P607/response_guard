"""``before_llm_request`` 事件处理器：为 NDFC 清理恢复痕迹并注入一次性提醒。

NDFC 收到外部恢复事件后，会把恢复提示当作一条 ``ROLE.USER`` payload 追加进
自己的内存链，而且不会主动移除。为了不让这段内部信号长期停留在发送视图里，
处理器在每次 NDFC 请求发出前做两件事：

1. 剔除所有携带内部恢复标记的历史 payload，使恢复痕迹永不进入模型上下文；
2. 若该流刚被请求恢复，则在本次请求末尾临时追加一条一次性提醒，重新说明
   回复质量要求。

两处改动都只作用于本次请求的 payloads：处理器构造新的列表并写回事件参数，
不修改 chatter 的会话状态、内存链、数据库或其它插件。提醒随请求生命期结束
自然消失，也不会被写回任何持久结构。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType, LLMPayload, ROLE, Text
from src.kernel.event import EventDecision

from ..config import ResponseGuardConfig
from ..runtime.ndfc_retry import (
    NDFC_RETRY_REMINDER,
    begin_retry_request,
    payload_carries_retry_marker,
)

logger = get_logger("response_guard")

_NDFC_REQUEST_NAME = "neo_default_chatter"
"""NDFC 主会话的 LLM 请求名（``BaseChatter.create_request`` 以组件名作默认值）。"""


class BeforeLLMRequestGuardHandler(BaseEventHandler):
    """在 NDFC 请求发出前过滤内部恢复痕迹并注入重试提醒。"""

    name = "response_guard_before_llm_request"
    description = "为 NDFC 过滤守卫恢复的内部标记，并按需注入一次性重试提醒"
    weight = 100
    """过滤与注入都要发生在请求真正发出之前，故先于其它订阅者执行。"""

    init_subscribe = [EventType.BEFORE_LLM_REQUEST]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """准备本次 NDFC 请求的 payloads。

        Args:
            event_name: 事件名，固定为 ``before_llm_request``。
            params: 事件参数，含 request_name / stream / payloads /
                meta_data 等字段。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 需要改动 payloads 时返回
            ``SUCCESS`` 并带回新列表；无需改动或本处理器不适用时返回 ``PASS``。
        """
        try:
            return self._prepare(params)
        except Exception as error:
            logger.error(
                f"response_guard NDFC 请求预处理异常，按放行处理: {error}",
                exc_info=True,
            )
            return EventDecision.PASS, params

    def _prepare(
        self, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行配置检查、请求过滤与 payload 改写。

        Args:
            params: 事件参数字典。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 处理结论与（可能已修改的）参数。
        """
        config = self.plugin.config
        if (
            not isinstance(config, ResponseGuardConfig)
            or not config.response_guard.enabled
            or not config.response_guard.guard_ndfc
        ):
            return EventDecision.PASS, params

        if str(params.get("request_name", "") or "") != _NDFC_REQUEST_NAME:
            return EventDecision.PASS, params

        if params.get("stream"):
            # 流式请求的 payloads 在发送前仍会变化，不参与本处理
            return EventDecision.PASS, params

        payloads = params.get("payloads")
        if not isinstance(payloads, list):
            return EventDecision.PASS, params

        filtered = [
            payload
            for payload in payloads
            if not payload_carries_retry_marker(payload)
        ]
        removed = len(payloads) - len(filtered)

        stream_id = _stream_id_of(params)
        reminder: LLMPayload | None = None
        if stream_id:
            inject, attempt = begin_retry_request(stream_id)
            if inject:
                reminder = LLMPayload(ROLE.USER, Text(NDFC_RETRY_REMINDER))
                filtered.append(reminder)
                logger.info(
                    f"NDFC Guard retry reminder injected stream={stream_id} "
                    f"retry={attempt}/{config.response_guard.ndfc_max_retries}"
                )

        if not removed and reminder is None:
            return EventDecision.PASS, params

        if removed:
            logger.debug(
                f"response_guard 已剔除 {removed} 条内部恢复标记"
                f"（stream={stream_id or '-'}）"
            )
        params["payloads"] = filtered
        return EventDecision.SUCCESS, params


def _stream_id_of(params: dict[str, Any]) -> str:
    """从事件参数中取出聊天流 ID。

    ``BaseChatter.create_request`` 会把 chatter 的 ``stream_id`` 注入请求
    ``meta_data``，事件参数原样透传该字典。

    Args:
        params: 事件参数字典。

    Returns:
        str: 聊天流 ID，取不到时返回空串。
    """
    meta_data = params.get("meta_data")
    if not isinstance(meta_data, dict):
        return ""
    stream_id = meta_data.get("stream_id")
    return stream_id if isinstance(stream_id, str) else ""
