"""被拦截响应的元数据隔离记录。

默认只保留可审计的元数据（时间、请求名、模型标识、判定、证据标签、长度与
内容哈希），不落正文与推理原文，避免在日志或内存快照中留下敏感内容。
``store_content`` 开启时才额外保留截断后的正文片段，供人工复核规则。
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass

from .verdict import Verdict

_EXCERPT_LIMIT = 160
"""``store_content`` 开启时单个文本字段保留的最大字符数。"""


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """一条被拦截响应的元数据记录。

    Attributes:
        timestamp: 拦截发生的 Unix 时间戳。
        request_name: 产出该响应的 LLM 请求名。
        model_identifier: 模型标识；未知时为空字符串。
        verdict: 判定结论。
        evidence: 触发拦截的证据标签。
        reply_length: 正文长度。
        reasoning_length: 推理正文长度。
        reply_hash: 正文的哈希摘要。
        reasoning_hash: 推理正文的哈希摘要。
        retry_index: 该响应在执行侧的守卫重试序号。
        reply_excerpt: 正文片段；仅在 ``store_content`` 开启时保留。
        reasoning_excerpt: 推理片段；仅在 ``store_content`` 开启时保留。
    """

    timestamp: float
    request_name: str
    model_identifier: str
    verdict: str
    evidence: tuple[str, ...]
    reply_length: int
    reasoning_length: int
    reply_hash: str
    reasoning_hash: str
    retry_index: int
    reply_excerpt: str | None = None
    reasoning_excerpt: str | None = None


class QuarantineStore:
    """固定容量的内存环形缓冲。"""

    def __init__(self, capacity: int) -> None:
        """初始化缓冲。

        Args:
            capacity: 保留的最大记录数，小于 1 时按 1 处理。
        """
        self.capacity = max(capacity, 1)
        self._records: deque[QuarantineRecord] = deque(maxlen=self.capacity)

    def append(self, record: QuarantineRecord) -> None:
        """写入一条记录，容量满时自动淘汰最旧的一条。"""
        self._records.append(record)

    def snapshot(self) -> tuple[QuarantineRecord, ...]:
        """返回当前全部记录的只读快照。"""
        return tuple(self._records)


_store: QuarantineStore | None = None
"""模块级缓冲。

Service 每次查询都会新建实例，隔离记录需要跨实例共享，因此存放在模块级；
容量变化时重建缓冲，历史记录随之清空。
"""


def record_block(
    *,
    request_name: str,
    model_identifier: str,
    verdict: Verdict,
    evidence: tuple[str, ...],
    reply_text: str | None,
    reasoning_text: str | None,
    retry_index: int,
    capacity: int,
    store_content: bool,
) -> QuarantineRecord:
    """记录一次被拦截的响应。

    Args:
        request_name: 产出该响应的 LLM 请求名。
        model_identifier: 模型标识。
        verdict: 判定结论。
        evidence: 触发拦截的证据标签。
        reply_text: 被拦截的正文，仅用于计算长度与哈希。
        reasoning_text: 被拦截的推理正文，仅用于计算长度与哈希。
        retry_index: 该响应在执行侧的守卫重试序号。
        capacity: 环形缓冲容量。
        store_content: 是否额外保留截断后的正文片段。

    Returns:
        QuarantineRecord: 写入的记录。
    """
    global _store
    if _store is None or _store.capacity != max(capacity, 1):
        _store = QuarantineStore(capacity)

    reply = reply_text or ""
    reasoning = reasoning_text or ""
    record = QuarantineRecord(
        timestamp=time.time(),
        request_name=request_name,
        model_identifier=model_identifier,
        verdict=verdict.value,
        evidence=evidence,
        reply_length=len(reply),
        reasoning_length=len(reasoning),
        reply_hash=_digest(reply),
        reasoning_hash=_digest(reasoning),
        retry_index=retry_index,
        reply_excerpt=_excerpt(reply) if store_content else None,
        reasoning_excerpt=_excerpt(reasoning) if store_content else None,
    )
    _store.append(record)
    return record


def quarantine_snapshot() -> tuple[QuarantineRecord, ...]:
    """返回已记录的拦截元数据。"""
    return _store.snapshot() if _store is not None else ()


def _digest(text: str) -> str:
    """计算文本的短哈希摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def _excerpt(text: str) -> str | None:
    """截断文本用于人工复核。"""
    if not text:
        return None
    return text[:_EXCERPT_LIMIT]
