"""response_guard 对外服务。

其他插件通过 Service API 获取本服务：

>>> from src.app.plugin_system.api.service_api import get_service
>>> service = get_service("response_guard:service:response_guard")
>>> verdict = await service.inspect(reply_text="...", tool_calls=response.call_list)

Service 每次查询都会新建实例，检测本身无状态；隔离缓冲由插件模块统一持有。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.app.plugin_system.base import BaseService

from .config import ResponseGuardConfig
from .quarantine import QuarantineRecord, quarantine_snapshot
from .runner import run_guard
from .verdict import GuardVerdict, PASS_VERDICT


class ResponseGuardService(BaseService):
    """检测明显属于模型/平台安全对齐的 OOC 拒答。"""

    name = "response_guard"
    description = "在发送与持久化之前检测并隔离模型层安全拒答"
    version = "1.0.0"

    async def inspect(
        self,
        *,
        reply_text: str | None = None,
        reasoning_text: str | None = None,
        reasoning_parts: Sequence[Any] = (),
        tool_calls: Sequence[Any] = (),
        request_name: str | None = None,
        retry_index: int = 0,
    ) -> GuardVerdict:
        """检测一轮 LLM 响应。

        Args:
            reply_text: 响应正文（``message``）。
            reasoning_text: 推理正文（``reasoning_content``）。
            reasoning_parts: 推理分段（``reasoning_parts``）。
            tool_calls: 本轮工具调用；其中 ``content`` 等可见正文参数会一并检测。
            request_name: LLM 请求名，用于日志与隔离记录。
            retry_index: 调用方的守卫重试序号。

        Returns:
            GuardVerdict: 三态检测结论；配置不可用或插件关闭时返回 PASS。
        """
        config = self._config()
        if config is None:
            return PASS_VERDICT
        return run_guard(
            config,
            reply_text=reply_text,
            reasoning_text=reasoning_text,
            reasoning_parts=reasoning_parts,
            tool_calls=tool_calls,
            request_name=request_name or "",
            retry_index=retry_index,
        )

    async def quarantine(self) -> tuple[QuarantineRecord, ...]:
        """返回最近被拦截响应的元数据记录。

        Returns:
            tuple[QuarantineRecord, ...]: 按拦截时间升序排列的记录快照。
        """
        return quarantine_snapshot()

    def _config(self) -> ResponseGuardConfig | None:
        """读取所属插件配置，类型不符时返回 None。"""
        config = self.plugin.config
        return config if isinstance(config, ResponseGuardConfig) else None
