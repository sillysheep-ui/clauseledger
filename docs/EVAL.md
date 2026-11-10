# 评测怎么做

> 一句话：**评测不是给回答打分，是在固定语料上跑固定用例，检查系统的最终状态与证据链是否与金标一致。**

所有比例的写法都是 `k/n = p [区间]`，不给裸点数。

---

## 0. 测什么、不测什么

| 层 | 对象 | 例子 | 能否机检 |
|---|---|---|---|
| L0 语料 | 覆盖面 | 上海落户有没有抓到 72 分细则的附件 | 能 |
| L1 抽取 | 条款、规则字段、引用位置 | 条款边界对不对、`degree>=master` 抽没抽到 | 能 |
| L2 判定 | 四态结论 | 这个人该不该判 `unsatisfied` | 能（有金标） |
| L3 系统 | 门控、幂等、恢复、成本 | 拒绝审批后有没有写副作用 | 能（看终态） |

**不测**：文案好不好、推荐排得对不对、用户满不满意、"这个 Agent 聪不聪明"。
理由见 `AUDIT.md` R4：没有标准答案的东西，评出来的数字只是把主观包装成客观。

---

## 1. 用例

一个用例一个 JSON，放 `evals/cases/<city>/<topic>/<id>.json`：

```json
{
  "case_id": "sh-hukou-007",
  "city": "sh",
  "topic": "hukou",
  "level": "L2",
  "source": "official_qa",
  "as_of": "2026-11-11",
  "question": "我是 2026 届硕士，学校在上海，签了上海的公司，能直接落户吗？",
  "profile": {
    "degree": "master",
    "graduation_year": 2026,
    "school_city": "sh",
    "employer_city": "sh"
  },
  "gold": {
    "outcome": "unsatisfied",
    "pins": [
      {"doc_no": "沪教委学〔2026〕13号", "clause_path": "二.(三)"}
    ],
    "must_cite": ["沪教委学〔2026〕13号#二.(三)"],
    "must_not_cite": [],
    "materials": ["学历证书", "劳动合同"]
  },
  "checks": ["outcome", "citation", "materials", "no_unsupported"]
}
```

三条硬规矩：

1. **金标绑 `(doc_no, clause_path)`，不绑内部 `clause_id`。** 解析器一升级 id 就变，金标会静默指错。运行时把 pin 解析成当前 id，解析不到就把用例标 `STALE`——**不算通过，也不算失败**，单独计数并进核验队列。
2. **`source` 决定可信度**：`official_qa`（官方问答原文，最硬）> `user_confirmed`（你逐条确认过）> `manual`（自己出的，只作补充）。报告里按来源分组给数，不许混在一起。
3. **`as_of` 必须写。** 政策有时效，金标 2026 年对，2027 年可能就错。语料版本变了要重跑核验。

### 50 条的构成

| 组 | 条数 | 用途 |
|---|---|---|
| 可解（有明确结论） | 18 | 主指标 |
| 不可解（`no_policy`，语料里没有） | 14 | 测"该拒答时拒答" |
| 部分可解（缺信息，应 `unknown` + 要材料） | 12 | 测"该问时问" |
| 行为约束（门控/幂等/恢复） | 6 | 测系统层 |

其中 **15 条 held-out**，开发期不跑，只在里程碑验收时跑一次。剩下 35 条可以天天跑。

---

## 2. 执行

```bash
python -m app.eval.run --suite gold --variant full --k 2 --workers 4
python -m app.eval.run --suite gold --variant baseline_no_rules --k 2 --workers 4
```

- `--variant full | baseline_no_rules | baseline_no_audit`：后两个只用于消融，不进日报
- `--k 2`：每条用例跑两次，算 `pass^2`；k 大了时间不够，k=2 是成本与稳定性的折中
- **确定性**：temperature=0、固定 seed、固定 `as_of`；**关闭 L4/L6 缓存**（命中会让 `pass^k` 虚高，看起来比实际稳）

产物目录（每次运行一个，不复用、不覆盖）：

```
eval_out/2026-11-11T02-15-00Z/
  manifest.json      git commit、corpus_version、模型 id、prompt hash、金标 sha256
  runs.jsonl         每个 run 一行
  cases/<case_id>/<run>.json   完整 trace + answer，失败时靠它定位
  summary.json
  report.md
```

目录已存在就报错退出——防止不小心覆盖上一次的失败证据。

---

## 3. 检查什么

### 3.1 结构检查（不通过 = `ERROR`）

| # | 检查 | 失败含义 |
|---|---|---|
| 1 | `answer` 含 `outcome/judgements/citations/materials/missing_evidence` | 系统没按契约输出 |
| 2 | 每条 citation 能定位到 `(doc_id, page, block_index, char_offset)` 且文字与原文一致 | 引用是编的或解析错位 |
| 3 | 每条 judgement 有 `rule_id`，每条 rule 有 `clause_id` | 证据链断了 |
| 4 | `outcome=satisfied` 时不存在 `unsatisfied` 的 judgement | 自相矛盾 |

结构错是**系统坏了**，不是判断错，必须和 `FAIL` 分开统计——否则修 bug 的进度会被算成"准确率提升"。

### 3.2 断言检查（不通过 = `FAIL`）

| # | 检查 |
|---|---|
| 1 | `answer.outcome == gold.outcome` |
| 2 | `must_cite` 全部出现在 citations 里 |
| 3 | `must_not_cite` 一条都不出现 |
| 4 | `materials ⊇ gold.materials`（允许多给，缺项记 `missing`） |
| 5 | `outcome=unknown` 时 `missing_evidence` 非空，且指出缺哪一条 |

### 3.3 行为检查（系统层用例）

不改对话、不看文本，只 dump mock 服务的终态：

| 场景 | 断言 |
|---|---|
| 拒绝审批 | mock 里 `0` 条待办 |
| 批准两次（重复提交） | `5` 条，不多不少（幂等键生效） |
| gate 处 `kill -9` 后恢复 | 仍 `5` 条，无重复、无丢失 |
| 先批准后拒绝 | 以先到者为准，副作用恰好一次 |
| 未经审批直接调 act | 事件流里必须出现 `interrupt`，且 act 未被调用 |

---

## 4. 指标

| 指标 | 口径 | 门槛 |
|---|---|---|
| `critical_false_satisfied` | 金标非 satisfied、系统判 satisfied 的条数 | **0（硬红线）** |
| `outcome_macro_f1` | 四态混淆矩阵的宏平均 F1 | ≥ 0.85 |
| `unknown_recall` | 该说"不知道"时说对的召回 | ≥ 0.80 |
| `citation_verifiable` | 引用可机检定位的比例 | **100%** |
| `unsupported_claims` | 无证据支撑的断言条数 | **0** |
| `materials_recall` | 应要材料被要到的比例 | ≥ 0.80 |
| `coverage` | 目标事项中有语料的比例 | ≥ 0.80 |
| `recovery` / `idempotency` | 3/3、0 违规 | 全过 |
| `pass^2` | 稳定性 | 报告不设门槛（观察项） |
| `p50 latency` / `tokens` | 单题耗时、单题 token | 预算内 |

**注意 `critical_false_satisfied` 和"漏判"不是一个量级**：漏判让人多跑一趟，误判让人白等一年。所以前者是 0 容忍，后者只进 F1。

---

## 5. 统计口径

| 问题 | 做法 |
|---|---|
| 一个比例该怎么报 | Wilson 95% 区间。`15/20 = 75% [53%, 89%]`——**不是 96%**（上一版我写错了）。`10/10` 的上界也只有 72% |
| 两个变体谁更好 | 同一批用例，精确 McNemar，报 `b/c` 与 p 值；p≥0.05 就不许说"更好" |
| 稳不稳 | `pass^k = C(c,k)/C(n,k)`；n=3 时"三次对两次"= 1/3，不是 2/3 |
| 分城市/事项要不要排名 | 每层 <5 条只列计数，不排名 |
| 失败怎么报 | 100% 归类，报频次。20 条失败里 8 条是"附件没解析"比"失败率 40%"有用得多 |

`app/eval/metrics.py` 已实现这几件事并有 27 个单测：

```python
wilson_interval(15, 20)      # (0.531, 0.888)
pass_k(3, 2, 2)              # 0.333
mcnemar_exact(b=2, c=12)     # p < 0.05
false_satisfied_indices(gold, pred)   # 致命错误单列
```

---

## 6. 反刷分

1. **held-out 15 条**开发期不碰，只在验收跑
2. **双向出题**：可解 + 不可解混排，防止"全说不知道"刷 `unknown_recall`
3. **判终态不判文本**：看 mock 状态和结构字段，不看措辞
4. **成本进判据**：超过 token 上限的 run 记为失败，不能靠"想很久"换准确率
5. **失败样本全保留**：`PASS+FAIL+STALE+ERROR = 总数`，报告里必须对账
6. **金标 sha256 进 manifest**：改金标让测试变绿会被发现
7. **judge 与被测模型不同**；消融时 judge 固定

---

## 7. 消融

| 对比 | 只改了什么 | 想证明 |
|---|---|---|
| `full` vs `baseline_no_rules` | 去掉规则层，全交给 LLM 判定 | 规则层值不值（主消融） |
| `full` vs `baseline_no_audit` | 去掉独立 Auditor | 信息隔离值不值 |

两次对比都保持检索、组装、核验完全一致，**只换一个变量**。

**预消融**：M2 一结束就用 15 条跑一次。如果 `baseline_no_rules` 只差 <10pp，说明"规则层"这个故事讲不圆，立刻换主战场——这一条写进 README，作为"如果输了怎么办"的预案。

---

## 8. 报告结构（`docs/eval_report.md`）

1. **第一句**：本报告只评测可机械判定的系统行为与事实准确性，不评测内容质量
2. 环境：manifest 摘要（commit、corpus_version、模型、金标 hash）
3. 主表：指标 / 值 / 区间 / n / 门槛 / 通过?
4. 四态混淆矩阵
5. 失败 Top 5 类别 + 每类一条代表样本（case_id + 一句原因）
6. 消融对比（b/c + p）
7. 已知局限 3 条（写在最后，不许藏）

---

## 9. 落地节奏

| 阶段 | 里程碑 | 做什么 |
|---|---|---|
| E0 | 现在 | `metrics.py` + 10 条占位用例 + 空跑骨架 |
| E1 | M1 | 结构检查 + 引用定位校验 |
| E2 | M2 | 判定矩阵 + 预消融（15 条） |
| E3 | M3 | 行为断言（门控/幂等/恢复） |
| E4 | M5 | 50 条全量 + 报告生成 + held-out 验收 |

## 10. 运行预算

50 条 × 2 次 = 100 run；单 run 约 12s，4 并发 ≈ 5 分钟。加上一次消融（100 run）仍在 30 分钟内。
超过 30 分钟就砍 k 或砍用例，**不许砍检查项**。
