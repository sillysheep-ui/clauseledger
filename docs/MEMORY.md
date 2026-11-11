# 记忆结构

> 一句话：**这不是聊天记录，是卷宗。** 记的是"用户说过什么事实、这句话在哪、什么时候说的"，不是"上次聊到哪了"。

## 0. 为什么这个项目的记忆不能按常规做法

大多数 Agent 的记忆是"把对话摘要塞进向量库，下次相似就捞出来"。这个项目不能这么做，原因很具体：

- 结论的代价是**用户据此辞职、迁户口、放弃一个 offer**。一条记错的事实（把本科记成硕士）会把 `unsatisfied` 翻成 `satisfied`，正好踩中唯一一条硬红线。
- 摘要不可回溯。用户问"我什么时候说过我交了社保"，系统要能指向**原话**，而不是一段模型改写过的概述。
- 政策会改版。存下来的结论在下一次修订后就是错的；如果记忆里存着结论，系统会拿旧结论当证据，形成自引用。

所以这里的记忆只有一条主线：**记输入和出处，结论每次重算。**

---

## 1. 四层，只有三层是记忆

| 层 | 存什么 | 存哪 | 生命周期 | 谁写 |
|---|---|---|---|---|
| **T0 运行态** | 图状态：slots、judgements、draft、citations | LangGraph checkpoint（Postgres） | 一次 run，可恢复 | 图节点 |
| **T1 会话** | 消息、澄清问答 | `messages` | 会话期，可删 | 节点 |
| **T2 档案** | 用户事实槽（学历、毕业年、社保月数…），每条带出处 | `profile_facts` | 长期，可删 | 抽取节点 + 用户确认 |
| T3 事实源 | 条款、规则、版本 | `documents` / `clauses` / `rules` | 长期，只追加 | ingest |

**T3 不是记忆。** 它是事实源，只被引用不被改写。把这条边界划清楚，才能回答"你的 RAG 和 memory 有什么区别"——RAG 检索的是**世界**，记忆存的是**你**。

---

## 2. 事实槽：四值语义，和判定同构

每个槽不是"有值 / 没值"，而是四种状态：

| status | 含义 | 例 |
|---|---|---|
| `known` | 用户说过 | "我是硕士" |
| `absent` | 用户明确说没有 | "我没交过社保" |
| `unknown` | 没提过 | 从没聊过社保 |
| `not_applicable` | 不适用 | 往届生问应届生条款 |

**「没说」和「说没有」在判定里是完全不同的输入。** 把两者合并，要么把缺失当满足（误判 satisfied），要么把沉默当否定（误判 unsatisfied）。这和判定层的四态是同一套哲学——不确定性必须显式存在，不能靠默认值消化。

## 3. 写入：只追加，不覆盖

```sql
create table profile_facts (
  id            uuid primary key,
  tenant_id     uuid not null,
  user_id       uuid not null,
  slot          text not null,          -- degree / graduation_year / social_insurance_months ...
  status        text not null check (status in ('known','absent','unknown','not_applicable')),
  value         jsonb,                  -- status=known 时才有
  source        text not null check (source in ('user','document','inferred')),
  evidence_ref  text,                   -- 消息 id 或条款 pin
  confidence    numeric,
  as_of         date not null,
  supersedes_id uuid references profile_facts(id),
  superseded_by uuid references profile_facts(id),
  created_at    timestamptz not null default now()
);
create index on profile_facts (tenant_id, user_id, slot, created_at desc);
```

用户改口（"我其实是硕士"）时**不 UPDATE 旧行**，而是插一条新事实、把旧行 `superseded_by` 指过去。当前值取 `superseded_by is null` 的那条。

代价是表会变大，收益是三件事：改口有痕迹、能评测"改口后结论是否翻转"、审计时能解释"当时为什么这么判"。

## 4. 写入的三道校验

1. **LLM 只提议，校验器落库**：`degree` 必须在枚举内、`graduation_year` 必须在合理区间，不合法一律落 `unknown`，不做"猜一个最接近的"。
2. **推断出来的事实不能单独支撑 satisfied**：`source=inferred` 的事实最高只能把结论带到 `unknown`，要用户确认才能升级。这条挡住了"模型自己推出来一个条件然后当成用户说的"。
3. **最小字段集**：只存判定需要的 10–12 个槽，不存简历全文。**每多存一个 PII 字段，就多一份责任**。

## 5. 读取：按 key 取，不按相似度取

**明确不用向量检索记忆。** 三个理由：

- 相似检索返回的是片段，不是"带 id 的事实"，引用链在这里断掉；
- 最近邻很可能捞到**已被 supersede 的旧值**；
- 检索错了没法解释——而"为什么用了这条记忆"和"为什么引这条条款"一样，必须能答。

读取就是一次按键 + 按时间排序的 SQL。真要做"我上次问过什么"，也是查 `runs` 表加时间过滤，不是相似度搜索。

## 6. 记忆会过期

事实有 `as_of`，规则有生效区间。两条规则：

- **跨会话回来**：如果最新事实早于语料版本变更日 180 天，重新确认时效性强的槽（社保月数、劳动合同、毕业年份），不直接复用。
- **语料版本变了**：同一用户同一事项的旧回答标"已过时"，重算。旧结论留在审计里，但**不允许被当作新结论的证据**（自引用计数必须为 0）。

## 7. 遗忘与审计的冲突

用户有权删记忆，审计要求留痕，两者直接冲突。解法是**把"能定位到人"和"审计需要的事实"拆开**：

- `profile_facts`、`messages`：可硬删（或加密后丢弃密钥）
- `runs`、`judgements`、`gate_decisions`：保留，但 `user_id` 用 HMAC 假名化，只存条款 pin、结论、时间
- 删掉记忆后，审计记录仍在，但**无法再关联到具体的人**

留存：消息 90 天，事实到用户删除为止，审计 2 年。

## 8. 会话内怎么压缩

- 最近 10 条消息原样保留；
- 更早的不做摘要，直接丢——**因为决策需要的信息已经在槽里了**，丢消息不丢状态；
- 如果一定要压 token，只压寒暄，且标注"非权威"；**永远不把结论写进记忆**。

这是"结构化状态 + 短上下文"的组合，而不是"长上下文 + 摘要"。后者在这个项目里是纯粹的风险。

## 9. 记忆怎么被评测

三条用例，全部可机检：

| 用例 | 断言 |
|---|---|
| **改口翻转** | 第一轮说本科→结论 X；第二轮说硕士→结论必须翻转，且旧事实仍在（supersede 链长度 2） |
| **跨会话重算** | 语料版本 V1 答过→ingest 到 V2（规则变了）→重开会话：trace 里 judge 节点必须重新执行，`answer.corpus_version == V2` |
| **记忆污染** | 所有 judgement 的证据引用必须指向 clause，指向历史消息/历史答案的引用数 = 0 |

指标：`profile_slot_accuracy`、`stale_answer_rate`（必须 0）、`self_citation_count`（必须 0）。

## 10. 不做什么

- 不做跨用户共享记忆（多租户隔离，一条 SQL 就能越界的事不做）
- 不让 Agent 自己决定记住什么——写入必须过校验器
- 不把对话摘要塞进向量库
- 不做偏好学习 / 个性化推荐
- 不记忆"用户上次的结论"——只记输入，结论重算

## 11. 落地顺序

| 里程碑 | 做什么 |
|---|---|
| M1 | T0 checkpoint + T1 消息 + 单轮跑通 |
| M3 | T2 事实槽 + supersede + 改口用例 |
| M5 | 过期重算 + 删除/审计分离 + RLS 策略 |
