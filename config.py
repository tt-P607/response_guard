"""response_guard 插件配置。

配置文件默认路径：config/plugins/response_guard/config.toml
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class ResponseGuardConfig(BaseConfig):
    """模型层拒答守卫配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "模型层拒答守卫配置"

    @config_section("response_guard")
    class GuardSection(SectionBase):
        """检测与隔离设置。"""

        enabled: bool = Field(
            default=True,
            description=(
                "是否启用模型层拒答守卫。关闭后不注册任何组件，"
                "调用方查询不到服务，等同于未安装。"
            ),
        )
        inspect_reasoning: bool = Field(
            default=True,
            description=(
                "是否检测模型的推理链。推理中明确形成「因安全约束拒绝当前请求」"
                "时判定为模型层拒答；关闭后只检测对外可见正文。"
            ),
        )
        guard_ndfc: bool = Field(
            default=True,
            description=(
                "是否通过 after_llm_request 事件为 neo_default_chatter 提供"
                "无侵入拦截。关闭后仅对外提供 Service，不影响事件链路。"
            ),
        )
        store_content: bool = Field(
            default=False,
            description=(
                "是否在隔离记录中保留被拦截正文的截断片段。"
                "开启后正文片段会进入内存缓冲，仅用于人工复核规则。"
            ),
        )
        quarantine_size: int = Field(
            default=20,
            description="内存隔离缓冲保留的记录条数，设为 0 表示不记录。",
            ge=0,
            le=500,
        )

    response_guard: GuardSection = Field(default_factory=GuardSection)
