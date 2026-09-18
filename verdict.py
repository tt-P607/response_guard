"""response_guard 的判定结果类型。

对外只暴露三态判定：``PASS``（放行）、``UNCERTAIN``（证据不足，按放行处理）、
``MODEL_REFUSAL``（明显的模型层安全拒答，应拦截）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    """一次响应的检测结论。"""

    PASS = "PASS"
    """未发现模型层拒答特征。"""

    UNCERTAIN = "UNCERTAIN"
    """出现了部分拒答特征但结构不完整；调用方应按放行处理。"""

    MODEL_REFUSAL = "MODEL_REFUSAL"
    """明显的模型/平台安全对齐拒答。"""


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """判定结论及其证据标签。

    Attributes:
        verdict: 判定结论。
        evidence: 支撑结论的证据标签，按检测顺序排列；同一标签只出现一次。
    """

    verdict: Verdict
    evidence: tuple[str, ...] = ()

    @property
    def blocks(self) -> bool:
        """是否应拦截本轮响应。"""
        return self.verdict is Verdict.MODEL_REFUSAL

    @property
    def is_uncertain(self) -> bool:
        """是否属于证据不足的模糊判定。"""
        return self.verdict is Verdict.UNCERTAIN


PASS_VERDICT = GuardVerdict(Verdict.PASS)
"""默认放行结论，供调用方直接复用。"""
