# DESIGN.md — 政策办事助手（G 方案）

> 状态：设计稿 v1。基于 2026-11-11 实测数据源。
> 一句话：**政策问答的正确答案不是一段话，是一个判定结果（适用 / 不适用 / 不确定）+ 依据条款 + 版本号。**

---

## 0. 边界（先写"我不是什么"）

| 是 | 不是 |
|---|---|
| 覆盖上海、杭州、成都 3 城 × 6 类事项（落户/补贴/档案/三方/社保/劳动权益） | 不是全国政策库；其余城市一律拒答 |
| 按生效日期选版本，答案标注"依据 X 年版第 Y 条" | 不是实时政策订阅服务 |
| 适用性判定：告诉你"你不满足哪一条" | 不给法律意见，不替代窗口咨询 |
| 只读用户主动提供的链接或文本 | 不爬登录墙后的内容，不存个人身份信息 |

**拒答不是失败，是设计。** 超出覆盖矩阵、无条款支持、槽位缺失，三种情况都走拒答/反问，且拒答要说明缺什么。

---

## 1. 系统架构

```
┌─ 采集层 ──┐   crawler / 手工导入 → raw_store（按 sha256 去重，原样冷存）
├─ 解析层 ──┤   类型分类 → 正文提取 → 条款切分 → 元数据抽取 → normalized
├─ 知识层 ──┤   Postgres + pgvector：documents / versions / clauses / embeddings
├─ 运行时 ──┤   LangGraph 状态机（Advisor ⇄ Auditor），PostgresSaver 持久化
└─ 审计层 ──┘   Langfuse/OTel span + runs/steps 表 + eval 报告
```

技术栈（锁版本，符合原始约束）：
`FastAPI` · `LangGraph 1.x` · `Postgres 16 + pgvector` · `Redis 7` · `ollama(qwen2.5:14b/32b)` + 云端 API 经 `LiteLLM` 网关 · `Langfuse`（本地 docker）· `uv`

---

## 2. 文档生命周期：保留什么、丢什么、怎么管

这一节回答"保留多少文件、丢掉多少文件"。

### 2.1 数量级（按 3 城 × 6 事项估算）

| 阶段 | 数量 | 动作 |
|---|---|---|
| 抓取候选页 | ~1,000 | 全部落 `ingest_steps` 日志 |
| 去重（content hash 相同） | −20% | **丢正文，留记录**（指向已有 version） |
| 导航/列表/栏目页 | −30% | **丢**，判据：链接密度 > 0.15 且正文 < 800 字 |
| 正文过短 / 解析失败 | −5% | **丢**，记失败码，可重试 |
| **保留正文** | **~450** | 正式文件 ~50、政策解读 ~150、PDF 附件 ~250 |
| 切成条款 | **4,000–6,000 条** | 全部入 `clauses` + embedding |

### 2.2 保留策略

| 资产 | 存在哪 | 存多久 | 能不能删 |
|---|---|---|---|
| 原始 HTML/PDF（sha256 命名） | 本地对象目录 | 永久 | 不能（审计要能回溯"当时抓的是什么"） |
| 解析后 Markdown + 条款 JSON | Postgres `clauses.raw_text` | 永久 | 随 version 归档 |
| 条款 embedding | pgvector | 随模型版本 | 换 embedding 模型时整批重建 |
| 历史版本 | `document_versions` 标 `superseded` | 永久 | 不删，但**退出主索引**（仍可回答"2025 年那版怎么规定"） |
| 抓取失败的页面 | 只存日志 | 90 天 | 可删 |
| 纯附件（报名表/图片） | 存，标 `non_policy` | 永久 | 不参与检索 |

**关键规矩：正文可以丢，证据不能丢。** 丢的是"这段 HTML 里的导航栏"，留的是"这份文件在某年某月某日长这样"的哈希与快照。

### 2.3 版本管理（这是准确率的第一道闸）

```sql
documents(id, tenant_id, url, doc_no, issuer, title)
document_versions(id, doc_id, fetch_time, content_hash,
                  effective_from, effective_to, status, raw_path)
-- status: draft | effective | superseded | expired
```

- 同一 URL 内容变了 → 新建 version，旧 version 置 `superseded`
- 从正文正则抽「自 X 年 X 月 X 日起施行」「有效期至 X 年 X 月 X 日」→ 自动填生效/失效区间
- 每次 ingest 结束生成新的 `corpus_version`（全局递增），**缓存与答案都挂这个版本号**

### 2.4 ingest 状态机（可断点续跑）

```
discovered → fetched → classified → parsed → normalized → chunked → embedded → indexed
                 ↘ rejected(code)      ↘ rejected(code)     ↘ rejected(code)
```

失败码：`HTTP_4XX` / `HTTP_5XX` / `TIMEOUT` / `NOT_ARTICLE` / `TOO_SHORT` / `PARSE_FAIL` / `DUP`。
每步落 `ingest_steps(status, error_code, attempt, duration_ms)`，支持从任意失败步重跑——**这套东西和你运行时状态机是同一套写法**，面试时可以串起来讲。

---

## 3. 知识组织：为什么必须是"条款级"

政策文件的天然单位是**条**，不是 512 token 的块。

```
clauses(id, version_id, article_no, paragraph_no, text, text_hash,
        city, category, applicability_json, embedding)
```

`applicability_json` 是规则层的核心：

```json
{
  "city": ["上海"],
  "hukou": ["非上海生源", "不限"],
  "degree": ["硕士及以上"],
  "graduation_year": {"gte": 2025, "lte": 2026},
  "social_insurance_months": {"gte": 6},
  "employer_type": ["重点机构", "不限"]
}
```

**检索用向量，判定用这张表。** 这一条就是"这不就是 embedding"的正面回答。

---

## 4. 运行时：LangGraph 状态机

### 4.1 State

```python
class PolicyState(TypedDict):
    run_id: str; thread_id: str; tenant_id: str
    question: str
    profile: dict          # city/hukou/degree/graduation_year/si_months/employer
    slots_conf: dict       # 每个槽位的置信度
    missing: list[str]
    intent: Literal["eligibility", "process", "general", "out_of_scope"]
    scope: dict            # corpus_version, cities, categories, as_of
    candidates: list[dict] # 检索候选（clause_id + score + 来源通道）
    judgements: list[dict] # {clause_id, verdict: satisfied/unsatisfied/unknown, why}
    conflicts: list[dict]
    draft: str; citations: list[dict]
    audit: dict | None
    gate: dict | None
    counters: dict         # retries / tokens / steps
```

### 4.2 图

```
START
 → normalize
 → extract_slots ──(缺槽位)──→ clarify(interrupt) ──┐
        │                                            │
        └→ plan_retrieval ←──────────────────────────┘
 → retrieve(混合召回)
 → judge_applicability(规则优先 + LLM 兜底)
 → check_conflict
 → compose
 → verify_citations
 → audit(Auditor agent, 独立上下文)
 → gate(interrupt, 条件触发)
 → finalize → END
```

条件边（全部落 trace，可回放）：

| 条件 | 走向 |
|---|---|
| `intent == out_of_scope` | 直接 `refuse`，不检索 |
| `missing != []` | `clarify`，一次只问一个，最多两轮 |
| 候选为空 | 放宽 scope 重试 1 次，仍空 → `refuse(insufficient_evidence)` |
| `unknown / total > 0.4` | 触发 `gate` 人工确认（半自动场景的默认行为） |
| `audit.verdict == "unsupported"` | 回 `compose` 重写一次，二次失败转人工 |
| 冲突未消解 | 答案里并列两个版本，标"存在冲突"，不选边 |

### 4.3 持久化与恢复

- `PostgresSaver` 作 checkpointer，`thread_id = 会话 id`
- 每个节点执行完落 checkpoint → `kill -9` 后 `graph.invoke(None, config)` 从最后节点续跑
- 所有写操作（提交办事清单、生成 PDF）带**幂等键** `run_id + step + target_hash`
- HITL 用 `interrupt()`，恢复用 `Command(resume=...)`；人工决定落 `human_gates` 表，含 `decided_at / decided_by / decision / reason`

---

## 5. 多 Agent：只用两个，理由是"信息隔离"

**不要堆 5 个 agent。** 本项目的多 agent 只解决一个单 agent 做不到的问题：**审核者不能被主 agent 的推理带偏**。

| Agent | 输入 | 输出 | 上下文 |
|---|---|---|---|
| **Advisor** | 用户问题 + 画像 + 检索到的条款 | 判定 + 草稿 | 可看全部推理链 |
| **Auditor** | **只给**：结论列表 + 每条引用的条款原文 | `supported / unsupported / cannot_judge` | **看不到** Advisor 的推理，看不到检索分数 |

- 两者用**不同模型**（Advisor 云端强模型，Auditor 本地 qwen）——降低同源偏差，也是"模型不锁定"的示范。
- **分歧率本身是一个评测指标**：Auditor 判 unsupported 的比例，人工抽检后可以量化"Composer 有多爱编"。
- 被否掉的方案：Planner/Retriever/Composer 各开一个 agent——它们之间没有信息不对称，拆开只会多花 token、多丢上下文，graph 节点足够。

---

## 6. 准确率：七道闸

| # | 闸 | 机制 | 过闸标准 |
|---|---|---|---|
| 1 | 元数据硬过滤 | 城市/事项/生效日期在 SQL 里先过，向量只在子集内比 | 召回集不含非目标城市 |
| 2 | 混合召回 | BM25 + 向量，RRF 融合，取 top-20 | 条款级 hit@5 ≥ 0.9（金标） |
| 3 | 规则判定 | `applicability_json` 与画像逐字段比对，产出三态 | 规则可覆盖的条款 100% 走规则，不走 LLM |
| 4 | LLM 兜底 | 仅处理无法结构化的条件，输出必须带"引用哪一条" | 无引用即判 unknown |
| 5 | 引用核验 | 每条断言回查 `clauses.text`，字符级包含 + 相似度 ≥ 0.92 双门限 | 引用伪造率 = 0 |
| 6 | 独立审核 | Auditor 只看结论和条文 | 分歧进人工，不自动发布 |
| 7 | 拒答 | 超范围/无证据/槽位缺失 | 三类拒答各有一条金标用例 |

**准确率不是"平均分 90%"，是"最危险的那一格为 0"。** 最危险的是：**把"不确定"答成"满足"**——用户会拿着这个去办事，白跑一趟。所以评测重点看混淆矩阵的 `unknown → satisfied` 那一格。

---

## 7. 缓存：六层，全部带版本号

政策类系统最危险的 bug 是"政策更新了，缓存还是旧的答案"。所以**缓存 key 必须包含 `corpus_version`**。

| 层 | Key | 存储 | TTL | 失效 |
|---|---|---|---|---|
| L1 原始文档 | `sha256(url+content)` | 本地文件 | 永久 | 内容变了才新增 |
| L2 解析结果 | `raw_hash + parser_version` | Postgres | 永久 | parser 升级 |
| L3 Embedding | `clause_hash + embed_model` | pgvector | 永久 | 换模型 |
| L4 检索结果 | `hash(norm_query + scope + corpus_version + index_ver)` | Redis | 1 天 | corpus_version 变化 |
| L5 LLM 响应 | `hash(prompt + model + temp)` | Redis | 7 天 | **只对确定性节点开启**（槽位抽取、类型分类） |
| L6 最终答案 | `hash(question + profile + corpus_version)` | Redis | 1 天 | corpus_version 变化 |

**故意不缓存的东西**：生成类节点（compose / audit）。理由：命中缓存会让 eval 看起来比实际稳，也会掩盖模型退化。宁可慢，不要"看起来一致"。

失效策略用**版本漂移**而不是主动删：新 corpus_version 一进来，旧 key 自然不再命中，后台按 TTL 自然回收。这样不需要复杂的缓存一致性协议。

---

## 8. 评测：50 条金标 + 一次消融

**金标构成**（50 条）：3 城 × 6 事项 × 各 2 条，加 14 条负样本（超范围 / 无政策覆盖 / 槽位缺失）。

**指标（6 条，不合成总分）**
1. 三态判定准确率 + 混淆矩阵（主看 `unknown→satisfied`）
2. 引用准确率（人工抽检 50 条引用，是否真的支持结论）
3. 地域混淆错误数（答案里出现非目标城市 → 机器可判）
4. 版本正确率（问 2026 届，是否引 2026 版）
5. 拒答准确率（14 条负样本）
6. 恢复幂等（注入故障 3 次，办事清单不重复执行，pass^3）

**消融实验（这是项目的命根子）**

| 系统 | 配置 |
|---|---|
| baseline | 纯 embedding 检索 + 单次生成，无规则层、无审核 |
| full | 元数据过滤 + 规则判定 + 审核 + 引用核验 |

报告：`地域混淆 40% → 0`、`unknown→satisfied 12% → 0`。**没有这张表，"这不是 RAG"这句话就是空的。**

报告口径：比例 + 95% Wilson 区间；n=50 时 40/50 的区间约 [66%, 89%]，别写"准确率 80%"。

---

## 9. 目录结构

```
app/
  main.py                 # FastAPI
  graph/state.py nodes.py edges.py build.py
  agents/advisor.py auditor.py
  policy/
    ingest/               # discover fetch classify parse chunk index
    rules/                # applicability DSL + matcher
    retrieval/            # hybrid + rrf
    versioning/           # corpus_version, effective dates
  store/
    pg.py redis.py cache.py
  eval/
    cases/50.jsonl  run.py  report.py
  observability/trace.py
infra/docker-compose.yml   # postgres + redis + langfuse
```

---

## 10. 工期（17 个工作日，比 D 多 2 天）

| 天 | 内容 |
|---|---|
| 1–2 | 采集 + 解析流水线（3 城，先跑通上海） |
| 3–4 | 条款切分 + 版本管理 + 建库 |
| 5–6 | 手写状态机（先不套 LangGraph）+ 槽位抽取 |
| 7–8 | 换 LangGraph + checkpoint + interrupt 恢复 |
| 9–10 | **规则层 DSL + 判定**（最容易低估，给足两天） |
| 11 | Auditor + 七道闸 |
| 12 | 缓存六层 + corpus_version |
| 13 | trace + 面板 |
| 14–15 | 50 条金标 + 消融实验 |
| 16–17 | 失败案例分析 + ADR + 录屏 |

**砍掉的**：MCP 工具化（v2）、双路由降级（只保留 LiteLLM 配置，不做演示）、Skill 市场。

---

## 11. 已知风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 金标要自己读政策（12–15h） | 最大工期风险 | 第 3 周就开始边读边标，不要留到最后 |
| 政策文件多为问答体，无条款号 | 引用定位失效 | 问答体单独建索引，引用退化为"第 N 问" |
| 地方站改版导致解析全挂 | 召回骤降 | parser 与站点解耦 + 解析失败率告警 |
| 规则 DSL 覆盖不全 | LLM 兜底变多，准确率掉 | 先做"能规则化的 20 类条件"，其余明确标 unknown |
| 用户画像要求填太多 | 体验差 | 槽位分批问，一个问题只问一个 |
