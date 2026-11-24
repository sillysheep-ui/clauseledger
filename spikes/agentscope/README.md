# Spike：AgentScope 2.0 当 agent 外壳

目的：验证「我们的条款检索」能不能塞进一个标准 agent 框架，以及它的权限/门控能不能承接我们的 HITL。

## 结论速览

| 项 | 结果 |
|---|---|
| 能跑通 | ✅ 3 个问题端到端 |
| 工具接入 | ✅ `FunctionTool` 包一层 `search_policy`，返回 `ToolChunk(content=[TextBlock])` |
| 权限系统 | ✅ 内置；自定义工具**默认 ASK**，需显式 `PermissionDecision(ALLOW)` |
| 多轮检索 | ✅ 模型自己改写 query 并二次检索（无需我们的 `llm_rewrite`） |
| 拒答 | ✅ 北京问题直接引用工具返回的拒答原因，没有编造 |
| 胶水代码 | 约 120 行（`spike.py`），不含检索实现 |
| 依赖隔离 | 必须单独 venv（AgentScope 会拉 60+ 包，和主项目依赖冲突） |

## 实测

| 问题 | 工具调用 | 结果 |
|---|---|---|
| 失业保险金能领多久 | 2 次（`失业保险金领取期限` / `…领取月数标准`） | 正确引用 E1/E2/E3，含 24 个月上限与延长规则 |
| 硕士毕业落户能加多少分 | 2 次 | 24 分 + 直接落户例外；主动声明「其他加分项证据不足」 |
| 北京落户需要几年社保 | 1 次 | `not_covered` 拒答，未编造 |

3 个问题总耗时 **61 秒**（含两次子进程检索 + DeepSeek 往返）。

## 踩到的四个坑

1. **API 是 async 的**：`agent.reply()` 是 coroutine，`Toolkit.get_tool_schemas` 是 async generator。
2. **自定义工具默认要审批**：`FunctionTool` 不传 `permission` 时返回 `PermissionDecision(ASK)`，agent 会停在「等待用户批准」。这本身是个好消息——它内置了我们要的 HITL。
3. **工具返回值必须是 `list[TextBlock]`**：传裸字符串会被当成"截断的文本"，模型会抱怨格式不对。
4. **依赖冲突**：AgentScope 2.0.11 会装 starlette 1.x / uvicorn 0.54 等，不能和主项目共用 venv，必须单独建。

## 对我们方案的意义

- **它补上了我们缺的「运行期控制流」**：模型自己决定检索几次、改几次 query。
- **它的 permission 机制可以承接门控**：把一个工具标成 ASK，就是一次「人在环」；这比我们手写 `PendingGate` 更通用。
- **判定仍应是工具，不是模型自由发挥**：`judge_luohu` 作为工具返回四态，模型只负责解释。

## 复现

```bash
uv venv spikes/agentscope/.venv --python 3.13
uv pip install --python spikes/agentscope/.venv/bin/python agentscope==2.0.11
spikes/agentscope/.venv/bin/python spikes/agentscope/spike.py "失业保险金能领多久"
```

---

# Spike 2：六工具 + 门控 + 20 题对照

## 20 题（覆盖 + 检索两个工具）

| 指标 | Agent (AgentScope) | Pipeline（11 步） |
|---|---|---|
| 金标文号出现在工具返回 | **16/20（80%）** | **16/20（80%）** |
| 两者不一致的题 | **0** | — |
| 平均工具调用 | 2.5 次/题 | 1 次检索 |
| 总耗时 | **1212 秒（≈60s/题）** | ≈30 秒（≈1.5s/题） |

结论：**agent 没有提升召回**——因为它调的就是同一个 `search_result()`。它的价值不在这里。

## 门控（draft_plan 设成 ASK）

```text
批准前待办条数：30
暂停在工具：draft_plan
批准后待办条数：33（新增 3）
同一批再写一次：{"applied": 0, "reused": 3}
最终待办条数：33
```

**ASK 权限 → 框架暂停 → 批准 → 恢复**，与我们的 `PendingGate` 语义一致；幂等仍由我们的 `actions.apply()` 保证。

## 一个必须记下来的坑：模型会编文号

- 第一版提示词没有约束 doc_no：模型写出 `沪府发〔2026〕落户办法-博士直落条款`、`沪人社规〔2026〕受理流程` 这类**不存在的文号**（`exists=false`）。
- 加了「doc_no 只能来自工具结果」后：模型把 doc_no 留空，并说「我不会为凑格式编造文号」。

两种都不是我们要的。正确做法：**工具返回值里就带上可用的文号/条款引用**，让模型只能抄，不能编。

## 判断

- ✅ AgentScope 能当编排层：工具、权限、流式事件、恢复都够用。
- ❌ 它不会让检索变准；20 题上与 pipeline 完全一致。
- ⚠️ 成本约 **40 倍**（60s vs 1.5s/题），必须加步数/工具调用/超时上限，或只对复杂问题启用。
- 📌 建议：保留 pipeline 作默认，agent 作 `CLAUSELEDGER_RUNTIME=agent` 的可选路径，用路由决定何时启用。
