# FOUNDATION · 底座设计

> 这份文档只回答一个问题：**底座凭什么可信**。
>
> 上层业务（条款判定）可以改，底座一旦定错，后面每一层都在补丁上盖楼。
> 每节固定四段：决策 / 依据 / 代价 / 失败模式。依据一律给外部出处，不给"经验上如此"。

---

## 0. 一句话

**一个 Postgres 库同时存状态、append-only 事件和幂等键；重放路径上只有"读已记录结果 + 纯计算"；外部副作用按至少一次设计，靠幂等键收敛成可观察的恰好一次。**

不做的事（明确排除，理由见各节）：

- 不上 Temporal / Kafka / ZooKeeper —— 单机单租户，Postgres 够用，多一个中间件就是多一个讲不清的组件
- 不上完整事件溯源（ES 框架）—— 事件表 + 周期快照即可，ES 的迁移成本换不来相应收益
- 不把 Langfuse / Phoenix 当审计真源 —— 它们是调试工具，保留策略和可变性方向与本项目相反

---

## 1. 执行模型

### 决策

- 不自建 durable execution 引擎，用 **LangGraph 1.2 + PostgresSaver**。
- 重放路径（节点被重跑时）**只允许两类操作**：读已记录的结果、纯计算（规则判定、模板组装）。
- 节点内部的执行顺序固定为：**admission（唯一键落库）→ 外部调用 → 落结果 → 提交**。
- 图版本（code commit sha）写进 run 头；版本不符时**默认拒绝自动恢复**，转人工确认。
- 节点内禁止非确定性读取（时钟、随机数、环境变量），必须在入口取一次写进 state。

### 依据

- Temporal 对确定性重放的定义：Workflow 必须 *"make the same Workflow API calls in the same sequence, given the same input"*，任何 LLM / HTTP / DB 调用必须放进 Activity，即 *"execute outside the replay path"*（[workflow-definition.md](https://docs.temporal.io/workflow-definition.md)）。
- Temporal 的 Activity 只保证 *"observed as completed exactly once"*，同时承认它 *"may be executed multiple times and may even partially complete more than once"*（[activity-definition.md](https://docs.temporal.io/activity-definition.md)）。
- LangGraph 的恢复粒度：*"you can only resume execution from a checkpoint (i.e., a super-step boundary)"*，节点级只写 `checkpoint_writes`，同一 super-step 内已完成的节点不重跑（[checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)）。
- Codex 用 append-only JSONL 做 rollout，resume 靠重放重建 model-visible history，并以最新 compaction 为边界（[recorder.rs](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/recorder.rs)）——说明"事件流恢复"在工业级实现里是可行的，不必上 Temporal。

### 代价

- 拿不到 Temporal 的确定性保护，只能靠"节点幂等 + 表记录"自己兜住；一个节点写错，代价是重跑的重复副作用。
- 每个节点多两次 DB 往返（读 key / 写 key），本地 ollama 下无所谓，接云端模型后仍是小头。
- 图结构必须保持"节点 = 一个可幂等单元"的纪律。想在一个节点里塞三次工具调用，就得先拆节点。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| 重跑后结果与上次不同 | 节点里有非确定性输入 | 所有非确定性输入在入口落 state，重跑读 state |
| 同一 LLM 调用被计费两次 | 崩溃在调用返回后、结果落库前 | 只影响成本不影响正确性；两次 attempt 归到同一 key，审计可查 |
| 恢复后图结构已变 | 代码更新 | run 头带 `graph_version`，不符则不自动恢复 |
| 节点重跑导致下游重复触发 | 节点没有把副作用收进幂等键 | 每节的外部写必须带 `Idempotency-Key`，见 §3 |

---

## 2. 状态模型

### 决策

- **Checkpoint 是恢复源，不是审计源。** 审计真源是我们自己的 `run_events`（append-only，只追加不修改）。
- **业务写入先于 checkpoint**（LangGraph 在节点返回后才写快照）→ 崩溃只会让 checkpoint 落后，不会出现"状态说有、实际没做"。
- 每个节点入口先写一行 `node_attempt(run_id, node, input_hash, attempt_no, started_at)`，再执行；结束写 `finished_at/status`。
- 单写者：会话级 `pg_advisory_lock(hashtext(run_id))` + `runs.epoch` 单调递增，所有写入的 UPDATE 带 `WHERE epoch = :epoch` 条件（fencing）。
- 大对象（PDF 原文、模型原始响应、长条款文本）**外置存储，表里只留引用**。

### 依据

- LangGraph 承认同步模式下*崩溃可能丢最近的 checkpoint*，且"首次 checkpoint 之前崩溃"与"从未调用"不可区分（[interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)）→ **不能把"checkpoint 存在"当作"副作用未发生"的证据**，所以要有 admission 行。
- Temporal Event History 是硬约束：payload 512 KB 告警 / 2 MB 报错，事件数 10,240 告警 / 51,200 报错并终止 Workflow（[defaults.md](https://docs.temporal.io/self-hosted-guide/defaults.md)）→ 大 payload 不能当审计账本。
- Codex 的 rollout 把命令输出和 MCP 结果各截断到 64 KB（`codex-rs/rollout/src/policy.rs:16-17`，标记 `"... command output truncated for persistence ..."`）→ **可回放 ≠ 可举证**，举证要另存原始载荷。
- Postgres advisory lock：会话级锁 *"held until explicitly released or the session ends"*，且 *"automatically cleaned up by the server at the end of the session"*（[explicit-locking](https://www.postgresql.org/docs/current/explicit-locking.html)）→ 崩溃不泄漏锁，不需要外部协调服务。
- 事件流与快照的关系：*"Snapshots are an optimization, not a replacement for the event stream"*，但 *"For most systems ... traditional data management is sufficient"*（[Event Sourcing pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)）→ 单库事件表 + 周期快照即可。

### 代价

- 状态存两份（checkpoint + run_events），需要一条对账查询保证不漂移，且要写进测试。
- 大对象外置 → 多一个 artifact 目录或表，多一套访问控制和清理逻辑。
- advisory lock 要求所有写者走同一入口；有人绕过 ORM 直连写库，单写者假设立刻失效。
- `epoch` 条件更新会在并发时分叉出"写入被静默丢弃"，必须显式检查 `rowcount`。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| checkpoint 与 run_events 对不上 | 两者不在同一事务 | 只允许"业务行 + 事件行"同事务提交；checkpoint 允许落后 |
| 恢复后重复执行节点 | 没有 admission 行，无法判断副作用是否已发生 | 节点入口强制写 attempt 行 |
| 旧进程复活后写入 | 暂停（非退出）的进程恢复了 | UPDATE 带 `epoch` 条件，旧 epoch 写不进去 |
| 事件表被"修正"导致举证失真 | 有人 UPDATE 历史行 | `run_events` 只允许 INSERT，权限层面禁 UPDATE/DELETE |

---

## 3. 副作用

### 决策

- 所有外部写操作走 **MCP 工具**，工具必须接受 `Idempotency-Key`，服务端按 key 返回首次结果。
- 节点内顺序固定：`admission(唯一键)` → 调用 → 落结果 → 提交。
- **outbox**：业务行 + 事件行 + outbox 行同一事务提交；relay 进程负责投递。
- 对外承诺的语义是"**可观察的恰好一次**"，物理执行一律按至少一次设计。

### 依据

- Temporal：exactly-once 只对"可观察结果"成立，物理执行可能多次（同 §1）。
- outbox pattern：*"Messages are guaranteed to be sent if and only if the database transaction commits"*，代价是 relay 可能重复投递、消费端必须幂等（[microservices.io](https://microservices.io/patterns/data/transactional-outbox.html)）。
- Stripe 幂等键：*"Retries with the same key return the saved response, including 500 errors."*，前提是 key 记录与副作用处于同一原子边界（[idempotent requests](https://docs.stripe.com/api/idempotent_requests)）。

### 代价

- mock 办理服务要自己实现幂等存储（一张 `effects` 表足够）。
- 接真实第三方时，对方未必支持幂等键，届时要退化成"先查后写"或人工对账。
- relay 进程多一个常驻组件，虽然很小。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| 同一次办理写了 10 条待办 | 调用方重试但服务端没做幂等 | 服务端按 `Idempotency-Key` 返回首次结果，键与写入同事务 |
| 事务提交了但消息没发 | relay 在提交后、投递前崩溃 | outbox 行还在，relay 重启后扫描重投 |
| 消息重复投递 | relay 至少一次 | 消费端幂等 |

---

## 4. 门控（人在环）

### 决策

- **两轴正交**：`capability`（技术上能做什么：只读 / 工作区写 / 全权）× `approval`（何时必须先问：`on-request` / `never` / `granular`）。
- 审批是**持久化状态机**，不是布尔值。`approvals` 表至少包含：`approval_id, run_id, node, param_hash, nonce, expires_at, consumed_at, decision, reviewer, comment`。
- 三件补丁：**TTL 到期即拒**、**单号单次消费**、**批准绑定参数哈希**。
- 工具注解只能**升档**（触发审批），绝不能**降档**（自动放行）。
- 权限边界落在**编排层 / 客户端**，不依赖 MCP server 的自述。
- 审批的写回通道与状态解耦：阻塞发生在调用栈，状态留在库里。

### 依据

- Codex 把安全拆成 sandbox mode（能力上限）与 approval policy（越界同意）两轴；`AskForApproval` 源码枚举已收敛为 `on-request`（默认）/ `never` / `granular`（五个布尔分别管 sandbox 审批、execpolicy、skill 脚本、request_permissions、MCP elicitation），**`untrusted` 已退役**（残留配置会导致客户端无法启动），`on-failure` 已无独立语义（[agent-approvals-security](https://learn.chatgpt.com/docs/agent-approvals-security)、[protocol.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs#L1181)）。
- LangGraph 的 `interrupt()` 恢复时 *"restarts the entire node from the beginning—it does not resume from the exact line where interrupt was called"*，官方要求 interrupt 之前的副作用必须幂等；多个 interrupt 按索引严格匹配，不可条件跳过或重排，`interrupt_before` 不推荐用于 HITL（[interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)）。
- HumanLayer 把审批建成云端状态机：客户端每 3 秒轮询直到 approved，对 agent 是阻塞调用，对平台是可查询记录（[humanlayer 0.7.9](https://pypi.org/project/humanlayer/0.7.9/)）。**它的缺陷正是我们要补的三件**：轮询无超时无上限；进程崩溃后云端记录仍在而执行栈已丢，批准了也没人执行；重复批准只是再写一次同一 status，没有 nonce / TTL / 参数哈希绑定；拒绝以字符串 "User denied …" 回给模型，模型可原样重试。
- MCP 2026-07-28 把权限边界分给两端：client 负责展示、提示、确认、超时、审计；server 只保证自己不越权；规范原文是*应当始终有人可拒绝工具调用*（[server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)）。
- `ToolAnnotations` 全为提示：`readOnlyHint` 默认 `false`、`destructiveHint` 默认 `true`、`idempotentHint` 默认 `false`，且**来自不可信 server 的注解必须当作不可信**（[schema.ts](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2026-07-28/schema.ts)）。
- OWASP AISVS 的四维（不可逆性、影响面、合规暴露、置信度）× 四档（读自动 / 写或 API 阈值内自动 / 资金转账与对外通信需人工 / 不可逆删除与安全配置强制复核），并要求审批展示**规范化完整参数**、加密绑定到参数与请求者、一次性 nonce、TTL 过期即拒（[C09-02](https://github.com/OWASP/AISVS/blob/34c052356fa4aca11f343fac2bb876547214f3f8/research/C09-02-High-Impact-Action-Approval.md)）。

### 代价

- 审批 UI 要展示完整参数（用户得看懂 5 条待办才能点批准），成本远高于一个"同意"按钮。
- TTL + 对账 + 补偿执行约 1 天工作量。
- `granular` 的五个开关如果全开，演示时会显得啰嗦；演示档只开其中两个，其余在 ADR 里说明。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| 批了没执行 | 消费后、执行前崩溃 | `consumed_at` 有值但 `effects` 无记录 → 对账任务补偿执行 |
| 执行了没批 | 有 `effects` 无对应 approval | 硬告警，视为严重缺陷 |
| 同一个批准用两次 | 没有单次消费 | `UPDATE ... WHERE consumed_at IS NULL RETURNING` 原子消费 |
| 批的是 A 材料、执行的是 B | 没绑定参数哈希 | `param_hash` 不匹配即拒绝 |
| 三天前批的现在还能用 | 没 TTL | `expires_at > now()` 进 WHERE |
| 工具自称只读就自动放行 | 信了注解 | 注解只升档；未知工具默认按破坏性处理 |

---

## 5. 观测

### 决策

- span 结构对齐 OTel GenAI semconv 四类：`chat`、`invoke_agent`（分 client / internal）、`execute_tool`、`plan`；命名为 `{gen_ai.operation.name} {gen_ai.agent.name}`。
- **正文与索引分离**：span 只存元数据与 `*_ref`，输入输出正文放 artifact 存储。
- **审计通道与调试通道分开**：审计 run 采样率恒为 1.0，调试通道可采样，两条管线互不影响。
- **不可变的 run 头**：单独一张表，记录 run_id、tenant、版本三元组、开始/结束时间、终态，只 INSERT。
- 脱敏**在采集侧完成**，用带密钥的确定性 HMAC（同一主体可跨 trace 关联，但不可反推）。
- 并发分支用"同一父下的兄弟 span + 精确时间戳 + span link"表达，不假装有顺序。
- Langfuse / Phoenix 只做可视化与调试，**不作为审计真源**。

### 依据

- OTel GenAI semconv 目前仍是 **Development** 状态，已独立成 `semantic-conventions-genai` 仓库，没有可固定的 Schema URL → 我们只能自己冻结字段字典并带版本号（[gen-ai-spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)）。
- OTel 明确规定 SHOULD NOT 默认采集指令与输入输出，给出三档：默认不记录 / 写 span 属性（仅合规场景）/ 上传外部存储只留 `*_ref` URI。**审计型 trace 走第三档是官方背书。**
- Langfuse 把 trace 级字段（user、session、tags、metadata）**反规范化复制到每条 observation**；查"当时的环境"读的是副本（[data-model](https://langfuse.com/docs/observability/data-model)）。
- Langfuse 的 `mask_otel_spans` 只能改属性，改不了 span 名、ID、父子关系与 resource 属性，且钩子抛错会丢弃整批导出（[masking](https://langfuse.com/docs/observability/features/masking)）。
- 采样发生在 trace 级，未命中的 trace 连同 observations 与 scores 都不上报，OTel 的采样决策同样被尊重（[sampling](https://langfuse.com/docs/observability/features/sampling)）→ **采样与"必须留痕"互斥**。
- PII 通用做法是 Presidio 一类算子：裸 hash 默认随机盐以抗暴力枚举，要跨记录关联同一主体必须显式提供一致 salt（[anonymizer](https://presidio.dataprivacystack.org/anonymizer/)）。
- OTel 每个 span 只有一个父，并发分支只能表达为兄弟 span，跨 trace 的因果关系用 span link（[traces](https://opentelemetry.io/docs/concepts/signals/traces/)）。
- Phoenix 要求每个 span 带 `openinference.span.kind`，反馈以 annotation 挂在 span / trace / session 三种粒度（[semantic_conventions](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)）——是"按 span 分型"的调试模型，对"一次 run = 一条可封存记录"支撑弱。

### 代价

- 两条采集管线，配置翻倍。
- 正文外置带来访问控制、清理策略、存储成本。
- 平台侧"顺手改个字段"的能力没有了——这是特意的。
- HMAC 密钥丢了就无法把两条 trace 关联到同一主体（可接受，不可逆性反而是特性）。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| 审计 run 没有完整 trace | 走了采样管线 | 审计 run 强制 `sample_rate=1.0`，独立 exporter |
| 举证时找不到原文 | 只存了截断/摘要 | 正文外置且不截断；span 里的 `*_ref` 必须可解析 |
| 平台删数据导致历史 run 失真 | 把平台当唯一副本 | 审计副本自存，平台可随时清空 |
| 两条 trace 关联不到同一用户 | 用了随机盐 hash | 用带密钥 HMAC，密钥与 trace 分开托管 |

---

## 6. 版本

### 决策

run 头必须钉死八项，缺一项即在评测里标为"不可复现"：

| 字段 | 来源 | 为什么要钉 |
|---|---|---|
| `corpus_version` | ingest 批次递增 | 条款变了，答案就该变；不钉就无法解释差异 |
| `parser_version` | 解析器版本 | 引用偏移依赖它，换解析器偏移全变 |
| `ruleset_version` | 规则抽取批次 | 规则层是判定主体 |
| `prompt_version` | prompt 模板版本 | 组装与审核的措辞会漂移 |
| `model_requested` | 我们请求的别名 | 如 `deepseek-flash` |
| `model_actual` | 响应里返回的模型 | 别名漂移的唯一证据 |
| `graph_version` | 代码 commit sha | 决定能否恢复 |
| `gold_version` | 金标集版本 | eval 结果不能跨金标版本比较 |

### 依据

- semconv 已标准化 `gen_ai.prompt.name` / `gen_ai.prompt.version` / `gen_ai.agent.version` / `gen_ai.response.model` / `gen_ai.response.id`，字段齐了也只能"重演"不能"复现"——`gen_ai.request.seed` 的官方措辞只是"更可能"相同（[registry/attributes](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)）。
- Temporal 要求变更用 `patched()` 或 Worker Versioning，否则 replay 直接报 non-deterministic error（[workflow-definition.md](https://docs.temporal.io/workflow-definition.md)）→ 图版本必须显式。
- Langfuse 的 masked 字段与副本语义（同 §5）→ 版本信息必须落在我们自己的 run 头，不能只存在平台里。

### 代价

- 每次 ingest、改 prompt、换模型都要递增版本；历史 run 会有相当比例标为"不可复现"。
- **这是诚实，不是缺陷**：报告里要单列"可复现 run 占比"。

### 失败模式

| 现象 | 原因 | 处置 |
|---|---|---|
| 换模型后结论变了但没人发现 | 只记了请求别名 | 记 `model_actual`，与 `model_requested` 不一致时告警 |
| 同一套 eval 数字跨月比较失真 | 金标或语料已更新 | 报告必须带 `corpus_version` / `gold_version` |
| 恢复后引用偏移全错 | 换了 parser 却复用旧偏移 | `parser_version` 不一致时重算引用 |

---

## 7. 崩溃点 × 恢复动作

按执行顺序排列，覆盖"状态已写/未写"与"副作用已发生/未发生"的交叉。

| # | 崩溃位置 | 库里状态 | 重启后动作 | 是否重复副作用 |
|---|---|---|---|---|
| 1 | 节点开始前 | checkpoint 停在上一个 super-step | 重跑该节点，无副作用 | 否 |
| 2 | admission 已写、调用未发 | attempt 行存在，无结果 | 同 key 重发调用 | 否（首次未发出） |
| 3 | 调用已返回、结果未落库 | attempt 行无结果 | 重跑会再调一次 | **是**，仅成本损失 |
| 4 | 业务写入已提交、checkpoint 未写 | 业务行在，事件行在（同事务） | 重跑节点，幂等键命中返回首次结果 | 否 |
| 5 | 事件已写、outbox 未投递 | outbox 行为 pending | relay 扫描重投 | 可能重复投递，消费端幂等 |
| 6 | `gate_action` 前 | 无 approval 行 | 重跑创建审批 | 否 |
| 7 | 已批准、未消费 | `consumed_at IS NULL` | 恢复时执行，消费 + 执行同事务 | 否 |
| 8 | 已消费、执行前 | `consumed_at` 有值，`effects` 无记录 | 对账任务补偿执行（同 key） | 否 |
| 9 | 执行完、回写前 | `effects` 有记录，run 未推进 | 同 key 调用返回首次结果，回写成功 | 否 |
| 10 | 双 resume 并发 | 两个进程同时恢复 | advisory lock 串行化，后者读到终态直接返回 | 否 |

**唯一无法消除的是第 3 行**：外部调用已发生但结果没落库时，重跑会再调一次。对 LLM 调用这是成本问题；对写操作，靠被调服务的幂等键兜住——这也是为什么 §3 要求所有工具都接受 `Idempotency-Key`。

---

## 8. 底座"算完成"的可测定义

不是"代码写完了"，是下面五条能跑通：

1. **拔电测试**：在 §7 的 10 个注入点各杀进程一次，重启后 run 全部到达终态，`effects` 计数不多不少。
2. **幂等测试**：同一个 `Idempotency-Key` 重放 3 次，外部写入恰好 1 次，返回值一致。
3. **单写者测试**：两个进程抢同一 run，只有一个能写，另一个拿到锁失败并被记录。
4. **对账测试**：`run_events` 的 step 集合与 checkpoint 的 super-step 集合一致；不一致时能定位到具体节点。
5. **观测测试**：任取一条审计 run，span 树完整，且每一个 claim 都能定位到 `(doc_no, clause_path, char_offset)`。

这五条写成 pytest，进 CI。**底座没有"差不多能跑"这个状态**——要么五条全绿，要么不算完成。

---

## 9. 这份文档的边界

- 它不解释业务（条款判定），那是 `ARCHITECTURE.md` 的事。
- 它不解释部署（容器、端口、make 目标），那是 `FRAMEWORK.md` 的事。
- 它的每条"依据"都指向外部原文；如果某条依据失效（比如 semconv 转正、Codex 枚举再改），改的是这里，不是代码注释。
