"""检测词表与预编译正则。

所有检测规则集中在本模块维护：中英文分组排列，正则全部预编译，检测函数
只消费这里导出的 ``re.Pattern``，不再就地拼装模式串。

词表只覆盖「模型/平台安全对齐」才会使用的表达。角色自身的拒绝（不要、
不想、不答应、换个话题等）不进入任何词表，因此天然不会被命中。
"""

from __future__ import annotations

import re

__all__ = [
    "COMPLY_DECISION_RE",
    "POLICY_REASON_RE",
    "REFUSAL_DECISION_RE",
    "REPLY_ASSISTANT_ACTION_RE",
    "REPLY_CONTENT_META_RE",
    "REPLY_NORMATIVE_REFUSAL_RE",
    "REPLY_SAFE_REDIRECT_RE",
    "REQUEST_REFERENCE_RE",
    "TOOL_TEXT_KEYS",
    "split_sentences",
]

# ── 句级切分 ────────────────────────────────────────────────────────────

_CJK_SENTENCE_SPLIT_RE = re.compile(r"[。！？；!?;\n]+")
"""中文标点与换行切分。"""

_LATIN_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
"""英文句号等号问号后跟空白时切分。"""

_SENTENCE_TRIM_CHARS = "\"'“”‘’`*-#> \t\r"
"""切分后需要剥离的行首标记与引号。"""


def split_sentences(text: str) -> tuple[str, ...]:
    """把文本切分为句子，用于判断决策方向落在哪一句。

    Args:
        text: 待切分文本。

    Returns:
        tuple[str, ...]: 已去除空白与列表标记的非空句子序列。
    """
    sentences: list[str] = []
    for chunk in _CJK_SENTENCE_SPLIT_RE.split(text):
        for part in _LATIN_SENTENCE_SPLIT_RE.split(chunk):
            cleaned = part.strip(_SENTENCE_TRIM_CHARS).strip()
            if cleaned:
                sentences.append(cleaned)
    return tuple(sentences)


# ── 词表 ────────────────────────────────────────────────────────────────

_POLICY_TERMS: tuple[str, ...] = (
    # 中文
    r"安全政策",
    r"安全规范",
    r"安全准则",
    r"安全要求",
    r"安全限制",
    r"安全考量",
    r"内容政策",
    r"内容规范",
    r"内容准则",
    r"内容限制",
    r"平台规则",
    r"平台政策",
    r"使用政策",
    r"社区准则",
    r"合规要求",
    # 英文
    r"safety polic(?:y|ies)",
    r"safety guideline(?:s)?",
    r"safety rule(?:s)?",
    r"safety restriction(?:s)?",
    r"content polic(?:y|ies)",
    r"content guideline(?:s)?",
    r"content restriction(?:s)?",
    r"usage polic(?:y|ies)",
    r"community guideline(?:s)?",
    r"polic(?:y|ies)",
    r"guideline(?:s)?",
    r"disallow(?:ed)?",
    r"prohibit(?:ed)?",
    r"not permitted",
)
"""外部安全约束词：模型/平台侧才会使用的规则词汇。"""

_REQUEST_REFERENCES: tuple[str, ...] = (
    # 中文
    r"用户(?:的)?(?:请求|要求|提问|问题|输入|指令)",
    r"对方(?:的)?(?:请求|要求)",
    r"当前请求",
    r"这个请求",
    r"该请求",
    r"此请求",
    r"这条请求",
    r"本次请求",
    r"这一请求",
    r"当前(?:提问|问题)",
    r"这个(?:提问|问题)",
    # 英文
    r"user'?s? request",
    r"current request",
    r"this request",
    r"the request",
    r"requested content",
    r"what the user (?:asked|requested)",
)
"""当前请求指涉词：把「拒绝」绑定到具体请求而非角色意愿。"""

_REFUSAL_DECISIONS: tuple[str, ...] = (
    # 中文
    r"(?:必须|应该|应当|需要|只能|要)(?:婉?拒|拒绝|回绝)",
    r"(?:不能|无法|不应|难以|不该)(?:回答|遵从|服从|提供|继续|满足|配合|完成)",
    r"(?:拒绝|回绝)(?:这个|该|此|当前)?(?:请求|要求)",
    # 英文
    r"must (?:refuse|decline|reject|deny)",
    r"(?:should|need to|have to) (?:refuse|decline|reject|deny)",
    r"(?:cannot|can't|can not|unable to) "
    r"(?:comply|provide|assist|continue|proceed|fulfill|answer|respond)",
    r"(?:refuse|decline|reject|deny) (?:this|the) (?:request|user)",
    r"must not comply",
)
"""最终拒绝决定词：表示「不满足当前请求」的决策。"""

_COMPLY_DECISIONS: tuple[str, ...] = (
    # 中文
    r"可以(?:继续|回答|正常|照常|满足|回复)",
    r"(?:能够|可以)(?:正常)?(?:回复|回答|继续)",
    r"依然可以",
    r"仍然可以",
    r"属于正常",
    # 英文
    r"(?:can|may) (?:still )?(?:continue|proceed|respond|answer|comply)",
    r"(?:is|are) allowed",
    r"no (?:safety |content )?polic(?:y|ies) (?:issue|violation|problem)",
    r"(?:fine|okay|ok) to (?:continue|answer|respond|proceed)",
    r"complies? with",
)
"""继续回答决定词：用于判断推理的最终决策方向是否已翻转为「可以回答」。"""

_REPLY_CONTENT_META: tuple[str, ...] = (
    # 中文：指示词 + 可选修饰 + 内容类名词
    r"(?:这类|这种|此类|该类|该种|这个|这些|那些|本次|当前)"
    r"[^。！？!?，,；;：:\n]{0,10}?"
    r"(?:内容|请求|要求|话题|文本|描述|题材|情节|场景|图片|图像|素材)",
    r"(?:该请求|该内容|该话题|该题材)",
    r"(?:你的|您的|对方的|他的|她的)(?:请求|要求|问题|提问)",
    # 英文
    r"(?:this|that|such|these|those|requested)[\sa-z']{0,16}?"
    r"(?:type of )?(?:content|request|material|topic|imagery|scenario)",
    r"your request",
)
"""请求/内容元话语：把对话内容当作「待处理的请求对象」来谈论。"""

_REPLY_ASSISTANT_ACTION: tuple[str, ...] = (
    # 中文
    r"(?:提供|生成|协助|帮助|帮忙|参与|回答|完成|处理|满足|配合|撰写|描写|创作|输出|执行|回复|续写|编写|绘制|表达)",
    # 英文
    r"(?:provide|generate|assist|help|engage|participate|answer|respond|complete|fulfill|create|write|produce|continue|comply|draft)",
)
"""助手行为词：以「提供服务」的口吻描述自身行为。"""

_REPLY_NORMATIVE_REFUSAL: tuple[str, ...] = (
    # 中文：否定 + 可选修饰 + 助手行为动词
    r"(?:无法|不能|不会|不可|没法|没办法|不应|不宜|不便|拒绝|恕难|难以)"
    r"(?:再)?(?:继续)?(?:(?:为|给|帮)[你您他她])?"
    r"(?:提供|生成|协助|帮助|帮忙|参与|回答|完成|处理|满足|配合|撰写|描写|创作|输出|执行|回复|续写|编写|绘制|表达)",
    r"(?:无法|不能|不会|不应)(?:再)?(?:继续|配合)(?:这|此|该|与)",
    r"(?:必须|只能)(?:婉?拒|拒绝)",
    # 英文
    r"can(?:not|'t| not) "
    r"(?:provide|assist|help|generate|create|engage|participate|comply|continue|proceed|fulfill|write|produce|answer|respond)",
    r"(?:unable|not able) to "
    r"(?:provide|assist|help|generate|create|engage|comply|continue|proceed|fulfill|answer|respond)",
    r"(?:must|have to|need to|will) (?:refuse|decline|reject|deny)",
    r"(?:refuse|decline) to (?:provide|assist|generate|comply|continue|engage)",
)
"""规范性拒绝：按规范/能力口径拒绝，而非角色意愿。"""

_REPLY_SAFE_REDIRECT: tuple[str, ...] = (
    # 中文
    r"(?:但|不过|然而|而是|或者)?(?:我们|我|咱)?(?:可以|能|愿意)(?:帮你|为您|为你)?(?:换|改)(?:成|为|个|一个)",
    r"(?:更|更为|更加)(?:健康|安全|合适|恰当|温和|友好|正面)",
    r"(?:换|改)(?:个|一个|成|为)(?:话题|方向|说法|版本|方式|内容)",
    # 英文
    r"instead,? (?:i|we) can",
    r"however,? (?:i|we) can",
    r"(?:i|we) can help (?:you )?(?:with|write|create|provide|draft)",
    r"safer (?:alternative|version|option|way)",
    r"more (?:appropriate|suitable|wholesome|healthier) (?:version|alternative|content)",
)
"""安全替代模板：拒答后立即给出「更安全/更合适」的替代方案。"""

TOOL_TEXT_KEYS = frozenset({"content", "text", "message", "reply"})
"""工具调用参数中承载「对外可见正文」的键名。

只有这些键下的字符串参与正文检测；thought / mood / reason 等内部字段
不属于实际发送的内容，不参与判定。
"""


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """把词表编译为单个大小写不敏感的正则。"""
    return re.compile("|".join(patterns), re.IGNORECASE)


POLICY_REASON_RE = _compile(_POLICY_TERMS)
REQUEST_REFERENCE_RE = _compile(_REQUEST_REFERENCES)
REFUSAL_DECISION_RE = _compile(_REFUSAL_DECISIONS)
COMPLY_DECISION_RE = _compile(_COMPLY_DECISIONS)
REPLY_CONTENT_META_RE = _compile(_REPLY_CONTENT_META)
REPLY_ASSISTANT_ACTION_RE = _compile(_REPLY_ASSISTANT_ACTION)
REPLY_NORMATIVE_REFUSAL_RE = _compile(_REPLY_NORMATIVE_REFUSAL)
REPLY_SAFE_REDIRECT_RE = _compile(_REPLY_SAFE_REDIRECT)
