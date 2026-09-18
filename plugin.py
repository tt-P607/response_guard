"""response_guard 插件入口。

对外提供两样东西：

- ``ResponseGuardService``：供 chatter 在发送前主动调用；
- ``AfterLLMRequestGuardHandler`` / ``BeforeLLMRequestGuardHandler``：通过
  ``after_llm_request`` 与 ``before_llm_request`` 事件，为不允许修改源码的
  chatter（NDFC）提供无侵入拦截与自动恢复。

三个组件共用同一份检测核心，插件本身不修改框架与其它插件。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import ResponseGuardConfig
from .handlers.after_llm_request import AfterLLMRequestGuardHandler
from .handlers.before_llm_request import BeforeLLMRequestGuardHandler
from .runtime.ndfc_retry import reset_all_retry_states
from .service import ResponseGuardService

logger = get_logger("response_guard")


@register_plugin
class ResponseGuardPlugin(BasePlugin):
    """模型层拒答守卫插件。"""

    plugin_name = "response_guard"
    configs = [ResponseGuardConfig]

    def get_components(self) -> list[type]:
        """返回插件组件。

        总开关关闭时不注册任何组件，调用方查询不到 Service，行为等同于未安装。
        """
        config = self.config
        if not isinstance(config, ResponseGuardConfig):
            logger.warning("response_guard 配置不可用，不注册任何组件")
            return []
        if not config.response_guard.enabled:
            logger.info("response_guard 已禁用，不注册任何组件")
            return []

        components: list[type] = [ResponseGuardService]
        if config.response_guard.guard_ndfc:
            components.append(AfterLLMRequestGuardHandler)
            components.append(BeforeLLMRequestGuardHandler)
        return components

    async def on_plugin_loaded(self) -> None:
        """记录插件加载状态。"""
        config = self.config
        if not isinstance(config, ResponseGuardConfig):
            logger.warning("response_guard 配置不可用，插件不生效")
            return
        guard = config.response_guard
        logger.info(
            f"response_guard 已加载（enabled={guard.enabled}, "
            f"guard_ndfc={guard.guard_ndfc}, "
            f"ndfc_retry_enabled={guard.ndfc_retry_enabled}, "
            f"ndfc_max_retries={guard.ndfc_max_retries}, "
            f"inspect_reasoning={guard.inspect_reasoning}）"
        )

    async def on_plugin_unloaded(self) -> None:
        """清空运行时重试状态，避免插件重载后残留旧预算。"""
        reset_all_retry_states()
