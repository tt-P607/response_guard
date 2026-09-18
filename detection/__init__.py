"""detection — response_guard 的纯代码检测核心。

模块分工：

- ``patterns``：词表与预编译正则，全部规则的唯一来源；
- ``reasoning``：模型推理链中的安全拒答判定；
- ``reply``：单段准备发送的正文的平台式拒答判定；
- ``detector``：把响应各字段拆成待检测文本并汇总为最终判定。
"""
