"""NDFC 守卫重试的运行时状态与固定文案。

response_guard 通过 ``after_llm_request`` 拦截 NDFC 的模型层拒答后，借助框架的
外部恢复入口请求该聊天流重新执行一次模型回合。本模块只承载这条恢复链的
跨事件状态与文本常量，不含任何检测规则。

**状态按 ``stream_id`` 隔离**：每个聊天流各自持有独立的预算与待注入标记，
多个群并发时互不影响。状态只存在于进程内存，不落库、不跨进程。

恢复链的生命周期：

```
拦截 → schedule_retry_request（预算 +1，标记待注入）
     → begin_retry_request（消费标记，注入一次性提醒）
     → 再次拦截 → 继续排入；守卫放行或预算耗尽 → reset_retry_state
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "NDFC_RESUME_PROMPT",
    "NDFC_RETRY_MARKER",
    "NDFC_RETRY_REMINDER",
    "NDFC_RETRY_SOURCE",
    "NdfcRetryState",
    "begin_retry_request",
    "can_schedule_retry",
    "finish_retry_request",
    "payload_carries_retry_marker",
    "reset_all_retry_states",
    "reset_retry_state",
    "schedule_retry_request",
]

NDFC_RETRY_SOURCE = "response_guard_retry"
"""``resume_chatter`` 的 source 取值，用于区分守卫触发的恢复与消息 / 定时恢复。"""

NDFC_RETRY_MARKER = "[[RESPONSE_GUARD_RETRY]]"
"""内部恢复标记，供 ``before_llm_request`` 精确识别并剔除对应的历史 payload。"""

NDFC_RESUME_PROMPT = (
    f"（内部恢复事件：上一条无效模型输出已丢弃。）{NDFC_RETRY_MARKER}"
)
"""交给 NDFC 作为恢复提示的文本。

NDFC 会把这段文本作为一条 ``ROLE.USER`` payload 追加进自己的内存链，且不会
主动移除，因此必须足够短、语义无害：即使被上下文压缩写成摘要，也不会改变
角色设定、剧情走向或安全状态，也不包含任何被拦截的正文。
"""

NDFC_RETRY_REMINDER = (
    "（系统提示：上一条回复未通过当前对话的响应检查，已被丢弃，不属于有效"
    "对话历史。请重新处理当前有效对话，保持既定角色身份、人设、语气、情绪与"
    "当前场景，自然继续当前互动，并通过当前 Chatter 的正常工具调用规范完成"
    "本轮响应。不要以助手、模型、平台或规则说明者身份解释上一条失败回复或"
    "内部检查。）"
)
"""恢复请求发出后，在下一次请求末尾临时追加的一次性提醒。

只纠正回复所处的角色状态，不涉及安全策略本身，也不包含任何绕过审核、要求
必须回答或禁止拒绝的内容。
"""


@dataclass(slots=True)
class NdfcRetryState:
    """单个聊天流的守卫重试状态。"""

    retry_count: int = 0
    """已经成功排入的恢复次数，初始请求不计入。"""

    pending_retry: bool = False
    """是否已请求恢复、正在等待对应的重试请求发出。"""

    retry_in_flight: bool = False
    """当前正在发送的请求是否属于恢复链。

    请求级事件在每个模型候选上都会触发一次，多模型 fallback 会让同一次请求
    多次经过 ``before_llm_request``。这个标记让后续候选不会把本次恢复误判成
    一次全新的正常请求。
    """


_states: dict[str, NdfcRetryState] = {}
"""按聊天流隔离的重试状态。"""


def begin_retry_request(stream_id: str) -> tuple[bool, int]:
    """登记一次即将发送的请求，决定是否为其注入一次性提醒。

    标记待注入时消费标记并保留本轮预算；否则视为一段恢复链之外的请求，
    连同上一轮可能残留的状态一起清空，使新的正常对话重新拥有完整预算。

    Args:
        stream_id: 聊天流 ID。

    Returns:
        tuple[bool, int]: 是否需要注入提醒，以及该请求对应的重试序号。
        不需要注入时序号为 0。
    """
    state = _states.get(stream_id)
    if state is None:
        return False, 0
    if state.pending_retry:
        state.pending_retry = False
        state.retry_in_flight = True
        return True, state.retry_count
    if state.retry_in_flight:
        # 同一次请求的后续模型候选，保持恢复链状态
        return False, 0
    _states.pop(stream_id, None)
    return False, 0


def can_schedule_retry(stream_id: str, max_retries: int) -> bool:
    """判断指定流是否仍有重试预算。

    Args:
        stream_id: 聊天流 ID。
        max_retries: 该流允许的重试次数上限。

    Returns:
        bool: True 表示还可以再排入一次恢复。
    """
    if max_retries <= 0:
        return False
    state = _states.get(stream_id)
    return state is None or state.retry_count < max_retries


def schedule_retry_request(stream_id: str) -> int:
    """登记一次已成功注入的恢复，并标记待注入提醒。

    Args:
        stream_id: 聊天流 ID。

    Returns:
        int: 该流累计排入的重试次数。
    """
    state = _states.setdefault(stream_id, NdfcRetryState())
    state.retry_count += 1
    state.pending_retry = True
    state.retry_in_flight = False
    return state.retry_count


def finish_retry_request(stream_id: str) -> None:
    """标记本次请求已经返回响应，结束恢复链的进行中状态。

    Args:
        stream_id: 聊天流 ID。
    """
    state = _states.get(stream_id)
    if state is not None:
        state.retry_in_flight = False


def reset_retry_state(stream_id: str) -> int:
    """清空指定流的重试状态。

    Args:
        stream_id: 聊天流 ID。

    Returns:
        int: 清空前的重试次数，0 表示此前没有排入过恢复。
    """
    state = _states.pop(stream_id, None)
    return state.retry_count if state is not None else 0


def reset_all_retry_states() -> None:
    """清空全部流状态，用于插件卸载与进程级重置。"""
    _states.clear()


def payload_carries_retry_marker(payload: Any) -> bool:
    """判断 payload 是否承载守卫恢复的内部标记。

    Args:
        payload: 待检查的 ``LLMPayload`` 或结构等价的替身对象。

    Returns:
        bool: True 表示该 payload 的文本内容包含内部恢复标记。
    """
    content = getattr(payload, "content", None)
    parts = content if isinstance(content, list) else [content]
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and NDFC_RETRY_MARKER in text:
            return True
    return False
