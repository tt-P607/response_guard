"""``after_llm_request`` 事件处理器：为 NDFC 提供无侵入拦截与自动恢复。

neo_default_chatter 不允许修改源码，因此改由框架的 after_llm_request 事件
接入：非流式请求下事件触发时完整响应已经存在，但 assistant payload 尚未写回
主链；此时清空 ``message`` / ``reasoning_content`` / ``reasoning_parts`` /
``tool_calls``，响应本身就不再具备可写回内容，NDFC 会自然走到「无工具调用」
分支，拒答既不会发给用户，也不会进入对话链。

清空之后，处理器还会尝试通过框架的外部恢复入口请求该聊天流重新执行一次
模型回合，使一次被拦截的拒答不至于变成「用户没有收到任何回复」。恢复只在
命中拦截时发生，且受独立预算约束；预算耗尽、恢复未生效或缺少 stream_id 时
保持原有行为：只拦截，由 NDFC 自然进入等待。

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
from ..runtime.framework_compat import resume_chatter_for_guard
from ..runtime.ndfc_retry import (
    NDFC_RESUME_PROMPT,
    NDFC_RETRY_SOURCE,
    can_schedule_retry,
    finish_retry_request,
    reset_retry_state,
    schedule_retry_request,
)

logger = get_logger("response_guard")

_NDFC_REQUEST_NAME = "neo_default_chatter"
"""NDFC 主会话的 LLM 请求名（``BaseChatter.create_request`` 以组件名作默认值）。"""


class AfterLLMRequestGuardHandler(BaseEventHandler):
    """在响应写回主链之前拦截 NDFC 的模型层安全拒答。"""

    name = "response_guard_after_llm_request"
    description = "检测 NDFC 响应中的模型层安全拒答，命中时清空本轮响应并按预算请求恢复"
    weight = 100
    """需要看到模型原始输出，故先于其它 after_llm_request 订阅者执行。"""

    init_subscribe = [EventType.AFTER_LLM_REQUEST]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """检测当前响应，命中时清空响应内容并按预算请求恢复。

        Args:
            event_name: 事件名，固定为 ``after_llm_request``。
            params: 事件参数，含 request_name / stream / message /
                reasoning_content / reasoning_parts / tool_calls / meta_data
                等字段。

        Returns:
            tuple[EventDecision, dict[str, Any]]: 命中时返回 ``SUCCESS`` 并在
            params 中带回清空后的响应字段；未命中或本处理器不适用时返回
            ``PASS``，不修改事件参数。
        """
        try:
            return await self._inspect(params)
        except Exception as error:
            logger.error(
                f"response_guard NDFC 拦截异常，按放行处理: {error}", exc_info=True
            )
            return EventDecision.PASS, params

    async def _inspect(
        self, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行配置检查、请求过滤、检测与恢复调度。

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
            self._finish_retry_chain(params)
            return EventDecision.PASS, params

        params["message"] = None
        params["reasoning_content"] = None
        params["reasoning_parts"] = []
        params["tool_calls"] = []
        await self._schedule_retry(params, config.response_guard)
        return EventDecision.SUCCESS, params

    @staticmethod
    def _finish_retry_chain(params: dict[str, Any]) -> None:
        """守卫放行后收束恢复链，清除该流的重试状态。

        Args:
            params: 事件参数字典。
        """
        stream_id = _stream_id_of(params)
        if not stream_id:
            return
        recovered = reset_retry_state(stream_id)
        if recovered:
            logger.info(
                f"NDFC Guard retry recovered stream={stream_id} after={recovered}"
            )

    async def _schedule_retry(
        self, params: dict[str, Any], guard_config: Any
    ) -> None:
        """按预算请求 NDFC 重新执行一次模型回合。

        被拦截的响应已经被清空，因此无论这里发生什么，拒答都不会重新发出去：
        任何失败都只是放弃恢复，不影响本次拦截。

        Args:
            params: 事件参数字典。
            guard_config: 配置中的 response_guard 分节。
        """
        stream_id = _stream_id_of(params)
        if not stream_id:
            logger.warning(
                "response_guard 无法请求 NDFC 恢复：事件参数不含 stream_id，"
                "本次仅拦截并等待"
            )
            return

        finish_retry_request(stream_id)

        if not guard_config.ndfc_retry_enabled:
            return

        max_retries = guard_config.ndfc_max_retries
        if max_retries <= 0:
            reset_retry_state(stream_id)
            return

        if not can_schedule_retry(stream_id, max_retries):
            logger.warning(
                f"NDFC Guard retry exhausted stream={stream_id} "
                f"max={max_retries}; blocked response discarded, "
                "falling back to Wait"
            )
            reset_retry_state(stream_id)
            return

        resumed = await resume_chatter_for_guard(
            stream_id,
            source=NDFC_RETRY_SOURCE,
            resume_prompt=NDFC_RESUME_PROMPT,
        )
        if not resumed:
            logger.warning(
                f"NDFC Guard retry skipped stream={stream_id}；"
                "恢复未生效，本次仅拦截并等待"
            )
            return

        attempt = schedule_retry_request(stream_id)
        logger.warning(
            f"NDFC Guard retry scheduled stream={stream_id} "
            f"retry={attempt}/{max_retries}"
        )


def _as_text(value: Any) -> str | None:
    """把事件参数规范成可检测的文本。"""
    return value if isinstance(value, str) and value.strip() else None


def _as_sequence(value: Any) -> tuple[Any, ...]:
    """把事件参数规范成序列，非序列值一律视为空。"""
    return tuple(value) if isinstance(value, (list, tuple)) else ()


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
