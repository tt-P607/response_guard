# response_guard

在响应进入发送与持久化链路之前，拦截其中**明显属于模型/平台安全对齐产生的
OOC 拒答**的 Neo-MoFox 插件。

## 它检测什么，不检测什么

**检测**：assistant 响应是否明显从「角色 / 正常聊天状态」切换到了
「AI / 平台 / 安全策略」层面的模型级拒答。

**不检测**：

- 用户输入是否敏感；
- 模型是否「应该」拒绝；
- 角色是否拒绝了用户；
- 回复内容是否安全。

正常角色拒绝一律放行，例如「不要。」「我不想。」「才不要呢。」
「今天不想理你。」「这种事情我不会答应。」「换个话题吧。」，以及由剧情、
人物情绪、关系和角色自身意愿产生的任何拒绝。

会拦截的是这类响应：

```
抱歉，我无法提供这类内容。
我不能协助生成此类内容，但可以帮助你改成更合适的版本。
根据安全政策，我无法继续处理这个请求。
I cannot comply with this request due to safety guidelines.
```

以及推理链中明确形成「当前请求 → 因安全政策 → 最终决定拒绝」的完整逻辑。

## 设计取向

**高精度优先，宁可漏判不误杀。** 判定只依赖确定性结构规则：出现了近似
结构但证据不完整时返回 `UNCERTAIN`，而 `UNCERTAIN` 与 `PASS` 等效——不确定
就放行。边缘审核回复可能被漏判，这是刻意的设计选择而非缺陷。

插件**不调用任何额外模型**：没有分类模型、没有 LLM Judge、没有 embedding，
全部是预编译正则与结构判断，零额外推理成本、零额外延迟。

## 判定结果

| 判定 | 含义 | 调用方行为 |
| --- | --- | --- |
| `PASS` | 未发现模型层拒答特征 | 正常放行 |
| `UNCERTAIN` | 有部分拒答特征但结构不完整 | 按放行处理 |
| `MODEL_REFUSAL` | 明显的模型层安全拒答 | 拦截并回滚本轮输出 |

## 检测规则

规则集中维护在 `detection/patterns.py`，中英文分组，正则全部预编译。

**正文检测**（`detection/reply.py`）——不因「抱歉」「不能」「无法」「危险」
「换个话题」单独出现而触发，只识别结构性组合：

```
内容元话语 + 规范性拒绝                              → MODEL_REFUSAL
规范性拒绝 + 安全约束                                → MODEL_REFUSAL
规范性拒绝 + 安全替代模板（缺内容元话语）             → UNCERTAIN
安全约束 + 助手行为口径                              → UNCERTAIN
```

**推理检测**（`detection/reasoning.py`）——按句判断决策方向：

```
存在安全约束词 + 存在请求指涉词 + 最终决策句落在「拒绝」  → MODEL_REFUSAL
存在安全约束词 + 最终决策句落在「拒绝」（无请求指涉）    → MODEL_REFUSAL
最终决策句落在「继续回答」                              → UNCERTAIN
只有拒绝措辞、没有安全约束支撑                          → UNCERTAIN
```

「考虑过安全问题，但最终决定可以回答」不会被误判。

**检测范围**：推理字段（`reasoning_content` / `reasoning_parts`）、响应正文
（`message`），以及工具调用中承载对外正文的参数（`content` / `text` /
`message` / `reply`）。`thought` / `mood` / `reason` 等内部字段不属于用户可见
内容，不参与判定。

## 接入方式

本插件对外提供两个接入面，两者共用同一份检测核心。

### KFC：Service 接入（完整集成）

`kokoro_flow_chatter` 在取得工具调用结果后、执行决策之前调用
`response_guard:service:response_guard`。命中后的处理：

1. 回滚本轮 assistant 输出，主链恢复到请求前基线；
2. 清空 `message` / `reasoning_content` / `call_list`；
3. 不执行动作、不发送平台消息、不产生工具回执；
4. 不提交 bot planning，拒答不进入 `mental_log` 与 context snapshot；
5. 原样重试一次（`guard_max_retries`，默认 1，与纯文本重试计数相互独立）。

重试**不注入任何越狱提示**：不加「必须回答」「忽略安全政策」，不做输入混淆、
编码转换或 prompt injection。本插件的职责是拒答隔离与状态保护，不是绕过审核。

第二次仍然命中时回滚并静默结束本轮，审核文本不会发给用户，也不会留下
「说过却无下文」的残缺历史。

### NDFC：事件接入（无侵入）

`neo_default_chatter` 不允许修改源码，因此改由框架的 `after_llm_request`
事件接入。非流式请求下事件触发时完整响应已经存在，但 assistant payload 尚未
写回主链；此时清空响应字段，响应本身不再具备可写回内容，NDFC 自然走到
「无工具调用」分支进入等待，拒答既不会发给用户，也不会进入对话链。

**NDFC 当前为非流式限定**：流式响应在事件触发时尚未产出内容，处理器会直接
跳过，不做任何判断。第一版不负责自动恢复，只负责拦截与防上下文污染。

事件处理器只处理 `request_name == "neo_default_chatter"` 的请求。KFC 走
Service 路径，两条路径职责分离，同一份响应不会被重复处理。

## 故障降级

守卫自身故障不会影响聊天：

- `response_guard` 插件未安装 → 调用方查询不到 Service，等同未安装；
- 插件已安装但总开关关闭 → 不注册任何组件；
- 检测过程抛异常 → 记录错误后返回 `PASS`，正常聊天不受影响。

## 安装

把插件目录放进 `plugins/`，重启应用即可。插件无第三方 Python 依赖，
无框架源码改动，安装与卸载都不影响其它插件。

默认只扫描 `kokoro_flow_chatter` 与 `neo_default_chatter` 的请求，不会
扫描所有 LLM 请求。

## 配置

配置文件：`config/plugins/response_guard/config.toml`

```toml
[response_guard]
enabled = true            # 总开关，关闭后不注册任何组件
inspect_reasoning = true  # 是否检测推理链
guard_ndfc = true         # 是否通过事件为 NDFC 提供无侵入拦截
store_content = false     # 隔离记录是否保留正文片段
quarantine_size = 20      # 内存隔离缓冲条数
```

KFC 的重试策略位于 KFC 自身配置：

```toml
[general]
guard_enabled = true      # 是否启用响应守卫
guard_max_retries = 1     # 命中后的原样重试次数上限，0 表示不重试
```

## 隔离记录

默认**不保存**被拦截响应的正文与推理原文，只保留可审计的元数据：时间、
请求名、模型标识、判定、证据标签、长度、内容哈希、重试序号。日志同样只输出
证据标签与长度，不输出整段文本。

元数据通过 Service 读取：

```python
from src.app.plugin_system.api.service_api import get_service

service = get_service("response_guard:service:response_guard")
records = await service.quarantine()
```

需要人工复核规则时，可临时开启 `store_content = true`，隔离记录会额外保留
截断到 160 字符的正文片段。

## 已知限制

- 只在非流式请求下生效；流式响应不参与检测；
- 中英双语词表为主，其它语种的审核式拒答可能漏判；
- 以「服务提供方」口吻用非常规措辞表述的拒答可能漏判；
- 角色设定本身就是 AI 助手时，「内容元话语 + 规范性拒绝」可能出现误判，
  此类场景建议关闭 `inspect_reasoning` 或调整词表；
- NDFC 命中后只等待，不自动重试。

## 许可

AGPL-3.0
