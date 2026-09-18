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

### NDFC：事件接入（无侵入 + 自动重试）

`neo_default_chatter` 不允许修改源码，因此改由框架的 `after_llm_request` 与
`before_llm_request` 两个事件接入。

**拦截**：非流式请求下 `after_llm_request` 触发时完整响应已经存在，但
assistant payload 尚未写回主链；此时清空响应字段，响应本身不再具备可写回
内容，拒答既不会发给用户，也不会进入对话链。

**恢复**：清空之后，插件通过框架的外部恢复入口（`resume_chatter`）请求该
聊天流重新执行一次模型回合，使一次被拦截的拒答不至于变成「用户没有收到任何
回复」。整个链路不修改 NDFC 源码，也不触碰它的内部状态：

```
命中 MODEL_REFUSAL
  ↓ 清空 message / reasoning_content / reasoning_parts / tool_calls
  ↓ resume_chatter(stream_id, source="response_guard_retry")
NDFC 自己从 WAIT_USER 回到 MODEL_TURN，重新请求 LLM
  ↓ before_llm_request：剔除内部标记 + 临时注入一次性提醒
重新检测新响应
```

NDFC 会把恢复提示当作一条 `ROLE.USER` payload 追加进自己的内存链且不会
主动移除。为了不让这段内部信号长期停留在发送视图里，`before_llm_request`
处理器会在**每一次** NDFC 请求发出前剔除所有携带内部标记的 payload；真正的
重试提醒只在恢复后的那一次请求里临时追加，请求发出后自然消失，不写回任何
持久结构。

**NDFC 当前为非流式限定**：流式响应在事件触发时尚未产出内容，两个处理器
都直接跳过，不做任何判断。

事件处理器只处理 `request_name == "neo_default_chatter"` 的请求。KFC 走
Service 路径，两条路径职责分离，同一份响应不会被重复处理。

### NDFC 重试预算

重试次数按**聊天流**独立计算，默认最多 3 次：

```
初始请求
+ Guard retry #1
+ Guard retry #2
+ Guard retry #3
= 最多 4 次模型生成
```

- 初始请求不计入重试次数；
- 纯文本拒答与工具调用内容拒答共用同一个预算；
- 预算耗尽后只拦截不再重试，不会因为模型持续拒答而无限循环；
- 恢复链中途守卫放行，或一段恢复链结束，都会清空该流的预算；
- 下一个普通请求重新拥有完整预算，一个群不会被上一次失败长期锁死。

## 故障降级

守卫自身故障不会影响聊天：

- `response_guard` 插件未安装 → 调用方查询不到 Service，等同未安装；
- 插件已安装但总开关关闭 → 不注册任何组件；
- 检测过程抛异常 → 记录错误后返回 `PASS`，正常聊天不受影响；
- 事件参数缺少 `stream_id`、恢复入口不可用、恢复调用抛异常或未生效 →
  **拦截照旧成立**，只是放弃自动重试，由 NDFC 自然进入等待。

重试是叠加在拦截之上的增强：任何环节失败都不会让已经拦下的审核回复
重新发出去，也不会伪造工具结果或用户消息。

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
ndfc_retry_enabled = true # NDFC 命中后是否请求框架重新生成一次
ndfc_max_retries = 3      # 每个聊天流的重试次数上限，0 表示不重试
log_content = true        # 命中时是否在日志中打印被拦截正文片段
store_content = false     # 隔离记录是否保留正文片段
quarantine_size = 20      # 内存隔离缓冲条数
```

KFC 的重试策略位于 KFC 自身配置，与本插件的 NDFC 重试完全独立：

```toml
[general]
guard_enabled = true      # 是否启用响应守卫
guard_max_retries = 1     # 命中后的原样重试次数上限，0 表示不重试
```

## 日志

命中拦截时默认会打印被拦截的正文，便于判断是真实拒答还是规则误伤：

```
[HH:MM:SS] response_guard | WARNING | response_guard blocked MODEL_REFUSAL
  request=neo_default_chatter evidence=reply_platform_refusal retry_index=0
[HH:MM:SS] response_guard | WARNING | NDFC Guard retry scheduled
  stream=<stream_id> retry=1/3
[HH:MM:SS] response_guard | INFO | NDFC Guard retry reminder injected
  stream=<stream_id> retry=1/3
[HH:MM:SS] response_guard | INFO | NDFC Guard retry recovered
  stream=<stream_id> after=1
```

预算耗尽时：

```
[HH:MM:SS] response_guard | WARNING | NDFC Guard retry exhausted
  stream=<stream_id> max=3; blocked response discarded, falling back to Wait
```

恢复入口不可用或未生效时：

```
[HH:MM:SS] response_guard | WARNING | NDFC Guard retry skipped
  stream=<stream_id>；恢复未生效，本次仅拦截并等待
```

**正文来源覆盖工具调用参数。** 模型往往不会直接输出纯文本拒答，而是把拒答
包装成工具调用——例如把「抱歉，我无法提供这类内容」作为 `action-kfc_reply`
的 `content` 参数传出。此时 `message` 可能为空或只含动作描写，只看 `message`
会什么都看不到。因此日志会收集全部对外可见文本：`message`，以及工具调用中
`content` / `text` / `message` / `reply` 参数下的字符串（含数组形式）。

`thought` / `mood` / `reason` 等内部字段不属于用户可见内容，不进日志。

换行与连续空白会归一化为空格，保证一条日志一行可读；单段超过 200 字符时
截断并标注「（已截断）」。

`log_content = false` 时日志只保留证据标签，不输出正文。

## 隔离记录

隔离缓冲默认**不保存**被拦截响应的正文与推理原文，只保留可审计的元数据：
时间、请求名、模型标识、判定、证据标签、长度、内容哈希、重试序号。

元数据通过 Service 读取：

```python
from src.app.plugin_system.api.service_api import get_service

service = get_service("response_guard:service:response_guard")
records = await service.quarantine()
```

需要额外留存正文时开启 `store_content = true`，隔离记录会保留截断到 160
字符的正文片段。隔离缓冲位于内存，进程重启后清空。

## 已知限制

- 只在非流式请求下生效；流式响应不参与检测；
- 中英双语词表为主，其它语种的审核式拒答可能漏判；
- 以「服务提供方」口吻用非常规措辞表述的拒答可能漏判；
- 角色设定本身就是 AI 助手时，「内容元话语 + 规范性拒绝」可能出现误判，
  此类场景建议关闭 `inspect_reasoning` 或调整词表；
- NDFC 的内部恢复提示会作为一条 USER payload 留在它自己的内存链里。
  该 payload 在**每次请求**都会被过滤掉，模型看不到它；但上下文压缩发生在
  过滤之前，压缩摘要仍有极小概率间接看到这段文本。这也是恢复提示被刻意
  写成「极短 + 语义无害」的原因：即使被写进摘要，也不会改变角色设定、剧情
  走向或安全状态。彻底消除这一点需要修改 NDFC 或框架源码，不在本插件范围内；
- 非公开入口 `ChatterManager.resume_chatter` 目前没有对应的 Plugin API，
  只能在 `runtime/framework_compat.py` 中直接引用（仓库已有多个插件这么做）。
  该引用被集中隔离在单一模块内，未来框架提供正式 API 时只需替换这一处；
- 重试状态保存在内存中，进程重启后清空。
- 日志中的正文是截断片段，无法用于完整还原被拦截内容。

## 许可

AGPL-3.0
