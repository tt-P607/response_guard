"""runtime — response_guard 的运行时支撑层。

只承载守卫命中之后的恢复流程所需的跨事件状态与框架入口封装，检测规则
全部保留在 ``detection/``。
"""
