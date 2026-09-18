"""``after_llm_request`` 事件处理器：为 NDFC 提供无侵入拦截。

neo_default_chatter 不允许修改源码，因此改由框架的 after_llm_request 事件
接入：非流式请求下事件触发时完整响应已经存在，但 assistant payload 尚未写回
主链；此时清空 ``message`` / ``reasoning_content`` / ``reasoning_parts`` /
``tool_calls``，响应本身就不再具备可写回内容，NDFC 会自然走到「无工具调用」
分支进入等待，拒答既不会发给用户，也不会进入对话链。

本处理器只处理 ``neo_default_chatter`` 的请求。KFC 侧走 Service 接入，两条
路径职责分离，避免同一份响应被重复处理。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType
from src.kernel.event import EventDecision

from ..config import ResponseGuardConfig
from ..runner import run_guard

logger = get_logger("response_guard")

_NDFC_REQUEST_NAME = "neo_default_chatter"
"""NDFC 主会话的 LLM 请求名（``BaseChatter.create_request`` 以组件名作默认值）。"""


class AfterLLMRequestGuardHandler(BaseEventHandler):
    """在响应写回主链之前拦截 NDFC 的模型层安全拒答。"""

    name = "response_guard_after_llm_request"
    description = "检测 NDFC 响应中的模型层安全拒答，命中时清空本轮响应"
    weight = 100
    """需要看到模型原始输出，故先于其它 after_llm_request 订阅者执行。"""

    init_subscribe = [EventType.AFTER_LLM_REQUEST]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """检测当前响应，命中时清空响应内容。

        Args:
            event_name: 事件名，固定为 ``after_llm_request``。
            params: 事件参数，含 request_name / stream / message /
                reasoning_content / reasoning_parts / tool_calls 等字段。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 命中时返回 ``SUCCESS`` 并在
            params 中带回清空后的响应字段；未命中或本处理器不适用时返回
            ``PASS``，不修改事件参数。
        """
        try:
            return self._inspect(params)
        except Exception as error:
            logger.error(
                f"response_guard NDFC 拦截异常，按放行处理: {error}", exc_info=True
            )
            return EventDecision.PASS, params

    def _inspect(
        self, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行配置检查、请求过滤与检测。

        Args:
            params: 事件参数字典。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 检测结论与（可能已修改的）参数。
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
            # 流式响应在事件触发时尚未产出内容，此处没有可检测的文本
            return EventDecision.PASS, params

        result = run_guard(
            config,
            reply_text=_as_text(params.get("message")),
            reasoning_text=_as_text(params.get("reasoning_content")),
            reasoning_parts=_as_sequence(params.get("reasoning_parts")),
            tool_calls=_as_sequence(params.get("tool_calls")),
            request_name=_NDFC_REQUEST_NAME,
            model_identifier=str(params.get("model_identifier", "") or ""),
        )
        if not result.blocks:
            return EventDecision.PASS, params

        params["message"] = None
        params["reasoning_content"] = None
        params["reasoning_parts"] = []
        params["tool_calls"] = []
        return EventDecision.SUCCESS, params


def _as_text(value: Any) -> str | None:
    """把事件参数规范成可检测的文本。"""
    return value if isinstance(value, str) and value.strip() else None


def _as_sequence(value: Any) -> tuple[Any, ...]:
    """把事件参数规范成序列，非序列值一律视为空。"""
    return tuple(value) if isinstance(value, (list, tuple)) else ()
