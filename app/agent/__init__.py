"""Agent 运行时：AgentScope 的 ReAct 循环 + 我们自己的工具层。

和 pipeline 的关系：同一批工具（`app.tools.service`）、同一份证据（`assemble()`），
区别只是"下一步做什么"由模型决定还是由固定状态机决定。
对外入口是 `app.agent.runtime.answer()`，返回纯 dict，不依赖 agentscope 的类型。
"""
