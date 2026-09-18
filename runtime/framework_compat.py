"""非公开框架能力的隔离封装。

``ChatterManager.resume_chatter`` 目前没有对应的 Plugin API，只能直接引用
``src.core.managers.chatter_manager``。整个 response_guard 中只有本模块接触这个
非公开入口：未来框架提供正式 API 时，替换本模块的实现即可，检测核心与事件
处理器都不需要改动。

恢复只是叠加在拦截之上的增强：无论导入失败、调用抛出还是返回未生效，函数
一律返回 ``False``，调用方保持已经拦截的响应不放行。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("response_guard")

__all__ = ["resume_chatter_for_guard"]


def _load_resume_entry() -> Callable[[], Any]:
    """惰性导入非公开的 Chatter 恢复入口。

    放在函数内导入，使框架未提供该入口时只影响自动重试能力，不会因为
    插件导入期报错而让整个插件加载失败。

    Returns:
        Callable[[], Any]: ``get_chatter_manager`` 调用入口。

    Raises:
        ImportError: 当前框架版本不提供该模块时。
    """
    from src.core.managers.chatter_manager import get_chatter_manager

    return get_chatter_manager


async def resume_chatter_for_guard(
    stream_id: str,
    *,
    source: str,
    resume_prompt: str,
) -> bool:
    """请求框架让指定聊天流的 chatter 重新执行一次模型回合。

    Args:
        stream_id: 聊天流 ID。
        source: 恢复来源标签，作为 ``WaitResumeEvent.source`` 传递给 chatter。
        resume_prompt: 随恢复事件交给 chatter 的提示文本。

    Returns:
        bool: True 表示恢复请求已成功注入；False 表示未生效，调用方应保持
        原有拦截行为。
    """
    if not stream_id:
        logger.error("response_guard 无法请求恢复：缺少 stream_id")
        return False

    try:
        get_chatter_manager = _load_resume_entry()
    except Exception as error:
        logger.error(
            f"response_guard 无法加载 resume_chatter，NDFC 自动重试不可用: {error}"
        )
        return False

    try:
        manager = get_chatter_manager()
        resumed = await manager.resume_chatter(
            stream_id,
            source=source,
            extra={"resume_prompt": resume_prompt},
        )
    except Exception as error:
        logger.error(
            f"response_guard 请求恢复失败（stream={stream_id}）: {error}",
            exc_info=True,
        )
        return False

    return bool(resumed)
