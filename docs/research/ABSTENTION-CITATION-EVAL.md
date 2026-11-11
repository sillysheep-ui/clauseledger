# 拒答与引用的评测方法

> 面向：中文政务政策问答（BM25 + 规则闸门，后续加向量/LLM）。
> 目标：把"拒答只有一个比例"换成一组**带分母、带区间、能定位到原因**的数。
> 一句话结论：**你现在的 15/15 拒答，只证明了失败率 ≤ 18.1%（n=15 零失败的单侧 95% 上界）。它没有证明任何关于"知道/不知道"的事。**

所有比例一律写成 `k/n = p [lo, hi]`，区间用 Wilson（n≥30）或 Clopper-Pearson（k=0 或 k=n）。

---

## 1. 选择性预测：risk–coverage、AURC、selective risk

### 结论

1. 标准三件套是 **coverage / selective risk / risk–coverage 曲线**，AURC 是这条曲线的积分。定义来自 El-Yaniv & Wiener (2010)，工程化在 Geifman & El-Yaniv (2017)。**不要自己发明指标名，直接沿用这套，审稿/汇报时零解释成本。**
2. **AURC 不适合当主指标**，尤其 n<200。它是一个积分，尾部（高 coverage 区）样本极少，方差爆炸，而且对分数打平（ties）敏感。**主指标应当是"固定 coverage 点的 risk + 区间"**：报 `risk@coverage=0.3/0.5/0.7/0.9`，AURC 只作为一个汇总数附 CI。
3. **小样本的正解不是 bootstrap，是 conformal risk control**（Angelopoulos et al., ICLR 2024）。它给的是**有限样本、分布无关**的保证：选阈值使 `(n/(n+1))·R̂ + 1/(n+1) ≤ α`，则真实期望风险 ≤ α。这是唯一能对小样本说硬话的方法，其余都是在估方差。
4. **你的系统里只有一种拒答是"选择性预测问题"**。这点必须先分清，否则整条曲线是错的（见 §2）：
   - "没接入这个城市/领域" —— 这是**索引台账事实**，不看问题内容就能判定，属于确定性闸门，不该进 risk–coverage。
   - "查过，没有政策" —— 这才是真正的选择性预测：检索器看了，判断"没有"。
5. 置信度不要用裸 BM25 分数。BM25 分数**跨查询不可比**（长查询天然高分、短查询天然低分），直接阈值化会在长问题上过度自信。至少要做**查询内归一化**（top1/topk 均值、top1−top2 margin），最好用 2–3 个特征拟合一个 logistic 校准器。Kamath et al. (2020) 在 QA 上的直接证据：OOD 下模型 softmax 严重过度自信，纯阈值只有 48% coverage @80% accuracy，换成训练过的校准器到 56%。

### 依据

| 文献 | URL | 关键定义 / 数字 |
|---|---|---|
| El-Yaniv & Wiener, *On the Foundations of Noise-free Selective Classification*, JMLR 11:1605–1641, 2010 | https://jmlr.csail.mit.edu/papers/volume11/el-yaniv10a/el-yaniv10a.html | 定义 selective risk `R(f\|φ)=R(f)/φ(f)`，coverage `φ(f)=P(f(x)≠⊥)`，risk–coverage 曲线，`AURC=∫₀¹R(c)dc`；证明最优拒答器 = 对后验风险阈值化（嵌套拒绝集）。**（正文 PDF 多次不可达，仅引定义，不引数字。）** |
| Geifman & El-Yaniv, *Selective Classification for Deep Neural Networks*, NeurIPS 2017 | https://arxiv.org/abs/1705.08500 | top-5 ImageNet：**以 99.9% 概率保证 2% 错误率时，覆盖率约 60%**（原文 "2% error … can be guaranteed with probability 99.9%, and almost 60% test coverage"）。方法：Softmax Response 基线 + SC-SVM，按目标风险反选阈值。 |
| Geifman & El-Yaniv, *SelectiveNet*, ICML 2019 | https://arxiv.org/abs/1901.09192 | 把问题写成 `min selective risk s.t. coverage ≥ c`（**coverage 约束**，不是风险约束），端到端训拒绝头；摘要称在分类/回归上一致改进 risk–coverage 权衡（无数值）。 |
| Kamath, Jia, Liang, *Selective QA under Domain Shift*, ACL 2020 | https://arxiv.org/abs/2006.09462 | **80% 准确率下回答 56% 的问题；直接用 softmax 只有 48%**。结论：OOD 上需训练校准器，且喂入其他域的 OOD 数据也有增益。 |
| Angelopoulos, Bates, Fisch, Lei, Schuster, *Conformal Risk Control*, ICLR 2024 | https://arxiv.org/abs/2208.02814 | 把 conformal 推广到**任意单调损失**的期望控制，紧到 `O(1/n)`，有限样本保证。实操式：`λ̂ = inf{λ : (n/(n+1))R̂ₙ(λ) + 1/(n+1) ≤ α}`。 |
| Kirichenko et al., *AbstentionBench*, 2025 | https://arxiv.org/abs/2506.09038 | 17 数据集 / 6 类不可答场景 / 35k+ 题 / 20 个前沿 LLM；**推理微调平均掉 24% 的弃权率**；模型规模几乎无影响。说明"能力上去了，拒答不会自动上去"，必须单独评。 |

### 落地建议

**记哪些字段**（每条 query 一行，落 `eval/reports/<ts>.rows[]`）：

```json
{
  "qid": "...", "cat": "literal|paraphrase|version|out_of_scope|cross_city|stale_trap",
  "in_scope": true,                  // (city,topic) 是否在索引台账里 —— 查表，不是模型判断
  "conf": {"top1": 12.4, "top1_norm": 0.83, "margin": 0.31, "cov_terms": 0.75, "gate": "pass"},
  "score": 0.83,                     // 送进曲线的单一标量（先用 top1_norm，后换 LR 校准值）
  "decision": "answer|refuse",
  "refusal_reason": "none|no_policy|not_covered",
  "gold": {"answerable": true, "index_has": true, "world_has": true},
  "outcome": "correct|wrong|over_refuse|correct_refuse|wrong_reason",
  "citation": {"n_cit": 2, "n_claim": 3, "clr": 2, "csr": 2, "ac": 2, "pad_ratio": 1.4}
}
```

**算什么数**：

1. `coverage = #answered / n_in_scope`。**分母只算 in_scope**，"没接入"的题不进分母，否则曲线被你索引的缺口污染。
2. `selective_risk(c) = #wrong among answered with score ≥ τ / #answered with score ≥ τ`，τ 由 coverage 反推。
3. 曲线画法：按 `score` 降序排序，从 k=1 扫到 n，每个 k 算 `coverage=k/n`、`risk=errors_k/k`。这就是经验 risk–coverage 曲线，不需要任何拟合。
4. `AURC = (1/n)·Σ_k risk_k`（等价于曲线下面积，梯形/矩形差 O(1/n)），并报 **E-AURC = AURC − AURC_oracle**（oracle = 把答对的排前面），消掉基础错误率的影响，否则两个准确率不同的系统没法比。
5. **报告表**（主表，替代单一 AURC）：

```
coverage   n_cov   errors   risk       95% CI            note
0.30        30       1      3.3%   [0.6%, 16.7%]    n 太小，仅参考
0.50        50       2      4.0%   [1.1%, 13.5%]
0.70        70       4      5.7%   [2.2%, 13.7%]
0.90        90       9     10.0%   [5.3%, 18.0%]     ← 建议的操作点
1.00       100      14     14.0%   [8.5%, 22.0%]
AURC = 0.081  [0.061, 0.104]   (paired bootstrap, B=2000)
```
6. **操作点用 conformal risk control 定，不要用眼睛挑**：目标 α=10%、n=100 时，覆盖集内最多允许 **9 个错误**（因为 `(100/101)·0.09 + 1/101 = 0.0990 ≤ 0.10`）；α=5%、n=100 时最多 **4 个**；α=10%、n=50 时最多 **4 个**。跑出来的阈值附带这句保证，比任何 CI 都硬。
7. 置信度演进路线：`top1_norm`（0 小时）→ `margin`（0.5 小时）→ 三特征 logistic（4 小时，需要 ≥100 条带标签的样本，按 query 分折交叉验证，**不要在同一批数据上拟合并报 AURC**）。

---

## 2. 两类拒答：合并成一个比率掩盖了什么

### 结论

1. 合并的拒答率**同时掩盖三件事**：(a) 拒得对不对；(b) 拒的理由对不对；(c) 拒是因为模型谨慎还是因为索引缺了一块。
2. **你现在的 50 条用例里根本没有"库内有、但确实没政策"这一类**（out_of_scope 15 条全部是范围外）。所以 `refused=15/15` 只测了"范围外识别"，而这是**查表就能 100% 做对**的事。真正难的"查过没有"一格是空的。这个数字目前是**自证清白**，不是评测。
3. 关键建模点：**"没接入"不是关于世界的判断，是关于我们索引的判断**。所以金标必须是二维的：

   | | 库内有该政策 | 库内无该政策 |
   |---|---|---|
   | **现实世界有政策** | `ANSWERABLE` | `NOT_COVERED`（答不了，且必须承认没接入） |
   | **现实世界无政策** | `NO_POLICY`（查过，确实空白） | `NOT_COVERED`（**不能说"查过没有"**，因为你没查） |

   右下角那格是最容易翻车的地方：明明没接入，却输出"经查，本市无此政策"——这是**编造检索行为**，比答错更严重（合规上等于伪造证据）。规则一句话：**`no_policy` 只在 `in_scope=true` 时允许出现。**
4. 因此该报的不是一个 3×3 混淆矩阵，而是**两个独立的二分类 + 一个理由归因**：
   - **决策层**：answer vs refuse，对 gold answerable / unanswerable。
   - **理由层**：在 gold unanswerable 且已拒的样本上，理由对不对。
   混在一个矩阵里，理由分类的错误会污染覆盖率指标，两个分母也不一样。
5. 最该盯的单一数字是**假"没接入"率** `P(refuse_not_covered | gold in_scope)`。它同时是信任杀手和产品 bug 信号（用户在覆盖范围内被系统甩锅）。

### 依据

| 文献 | URL | 关键数字 / 定义 |
|---|---|---|
| Peng, Choubey, Xiong, Wu, *Unanswerability Evaluation for RAG*（UAEval4RAG）, 2024 | https://arxiv.org/abs/2412.12300 | 六类不可答：Underspecified / False-presupposition / Nonsensical / Modality-limited / Safety / **Out-of-Database**。600 条合成请求，27 种组件组合。指标：Acceptable Ratio、Unanswered Ratio、Ask-for-Clarification Ratio、`Joint = 0.7·Correctness + 0.3·Acceptable`。**TriviaQA 行：不可答总体 Acceptable 49.0%，但分类别 24.0% / 87.0% / 51.0% / 10.0% / 46.0% / 76.0% —— 聚合值抹掉了 77pp 的类别差。** |
| Kirichenko et al., *AbstentionBench*, 2025 | https://arxiv.org/abs/2506.09038 | 用 abstention recall / precision / F1；**精度普遍很高、召回很低**，所以主看 recall。即"该拒没拒"是主要失败模式，而不是"不该拒却拒了"。 |
| Magesh et al., *Hallucination-Free?*, 2024 | https://arxiv.org/abs/2405.20362 | 三个商用法律 RAG **17%–33% 的时间产生幻觉**；Lexis+ AI 准确 65%；Westlaw AI-AR 准确 42% 但幻觉率约两倍；Ask Practical Law AI **60%+ 查询为 incomplete（拒答或 ungrounded）**。 |
| 本仓库实测 | `eval/reports/20261112-002724.json` | out_of_scope n=15 refused=15；无 `refusal_reason` 字段；`answered` 恒为 0，无分母。 |

### 落地建议

**记哪些字段**：用例侧加二维金标

```json
"gold": {
  "index_has": true,     // 语料里到底有没有这份文件（可机检：查台账）
  "world_has": true,     // 现实世界有没有这条政策（人判，写 source 和 as_of）
  "outcome": "answerable|no_policy|not_covered",
  "expected_reason": "none|no_policy|not_covered",
  "must_not_say": ["本市无此政策"]   // 当 index_has=false 时的强制负例
}
```
输出侧加 `refusal_reason` 与 `in_scope`，并且 `in_scope` 必须来自 `eval/scope_manifest.json`（city→topic→doc_count），不是模型自述。

**算什么数（报这张表）**：

```
                           gold answerable   gold no_policy   gold not_covered
pred answer                    TP_ans            FP_ans            FP_ans
pred refuse/no_policy        over_refuse      correct_no_pol      WRONG_REASON
pred refuse/not_covered      WRONG_REASON     WRONG_REASON       correct_nc
```

五个派生比率，各自带 k/n 和区间：

| 指标 | 定义 | 目标 |
|---|---|---|
| 应答率（1−过度拒答） | `P(answer \| gold answerable)` | 越高越好，设下限 |
| 幻觉应答率 | `P(answer \| gold unanswerable)` | 越低越好，设上限 |
| 理由准确率 | `P(reason=gold \| gold unanswerable ∧ refused)` | 越高越好 |
| **假"没接入"率** | `P(refuse_not_covered \| gold in_scope)` | **应为 0（硬检查，不是统计）** |
| 假"查过没有"率 | `P(refuse_no_policy \| gold index_has=false)` | **应为 0（硬检查）** |

后两个不要当比率报，当**断言**报：它们是索引台账的函数，出现一次就是 bug，直接 fail CI。这样"没接入"这一类就不需要 200 条样本来估，只需要台账正确。

**分层**：五个比率必须按 `city` 和 `topic` 分开报。UAEval4RAG 那个 24% vs 87% 就是警告——政务场景里"劳动"和"落户"的空白率天差地别，聚合数没有决策价值。

---

## 3. 引用评测：ALCE / AIS / RAGAS 怎么选

### 结论

1. **主口径用 ALCE 的结构，但把它的 NLI 判定换掉。** ALCE 是唯一一个"按 statement 拆、按 citation 计分、并且公布了与人工一致性"的方案（κ=0.698/0.525，判定准确率 85.1%/77.6%），结构可以直接搬。
2. **RAGAS 的 faithfulness 不能当引用指标。** 它评的是"答案是否 grounded 在 context 里"，**不评"哪条引用支撑哪句话"**。在你这种"必须指到条款+字符区间"的场景里，RAGAS 会把"引了 A 条款但结论来自 B 条款"判成满分。
3. **ALCE 和 RAGAS 都漏掉一个维度：文件本身适不适用。** 这是 Magesh et al. 的 `misgrounded`：引用真实、逐字正确、但引的是**已失效版本**或**别的城市**的文件。CS 意义的 grounded 给满分，法律意义上是幻觉。
4. 所以你的口径应该是**三层**，而不是一个分数：

   ```
   L1 可定位 Locatable   ：引用的字符区间在原文里逐字存在        —— 机械，目标 100%
   L2 能支撑 Sufficient   ：该片段（单独）能推出结论的每条原子主张 —— ALCE recall 的语义
   L3 适用   Applicable   ：该文件在 as_of 时点有效、且适用于该城市/人群 —— 机械（查效力表）
   ```
   再补一个**结论级**判定：所有引用片段合起来能否推出结论（这是 L2 之上、防"断章取义"的那道闸）。
5. ALCE 的 citation precision 有个反直觉但重要的设定：一条引用判为"不相关"需要**两个条件同时成立**——(a) 它单独不支撑该 statement，(b) 去掉它之后剩下的引用**仍然**支撑。也就是说 **ALCE 不要求最小引用集**，冗余引用不扣分。这对政务场景是对的（人写公文也常并列引用），照抄这个设定，别自己发明"引用越少越好"。

### 依据

| 文献 | URL | 关键定义 / 数字 |
|---|---|---|
| Gao et al., *ALCE: Enabling LLMs to Generate Text with Citations*, EMNLP 2023 | https://arxiv.org/abs/2305.14627 （代码 github.com/princeton-nlp/ALCE） | **citation recall**：对每个 statement `sᵢ` 取 0/1，要求 `Cᵢ≠∅` 且 `φ(concat(Cᵢ), sᵢ)=1`，再对全部 statement 平均；`φ` 用 NLI（`google/t5_xxl_true_nli_mixture`）。**citation precision**：对每条 citation 取 0/1 再平均；判 irrelevant iff `φ(c,sᵢ)=0` **且** `φ(concat(Cᵢ\{c}),sᵢ)=1`。数字：**ELI5 上最好的模型仍有 50% 的生成缺乏完整引用支撑**；与人工一致性 κ=**0.698**（recall，substantial）/ **0.525**（precision，moderate）；以人工为金标的判定准确率 **85.1% / 77.6%**。§3.4 专测"top-1 段落直接当答案并自引"和"整段照抄"两种刷分，指标不虚高。 |
| Rashkin et al., *Measuring Attribution in NLG Models*（AIS）, Computational Linguistics 49(4):777, 2023 | https://arxiv.org/abs/2112.12870 | `(s,t)` 对 `P` 归因 iff "a generic hearer will, with a chosen level of confidence, affirm: 'According to P, s'"；`s` 按时间 `t` 解释；用 explicature 处理指代/省略。**明确不判断事实真伪，只判断是否由 P 支撑**；source quality 是另一个维度。 |
| Es et al., *RAGAS*, EACL 2024 demo | https://arxiv.org/abs/2309.15217 | `Faithfulness = |V|/|S|`（LLM 拆 statement 后逐条判能否由 context 推出）；Answer Relevance = 反生成问题的 embedding 余弦均值；Context Relevance = 相关句数/总句数。与人工 pairwise 一致率 **0.95 / 0.78 / 0.70**（GPT Score 基线 0.72/0.52/0.63）。**不评 citation 归属。** |
| Magesh et al., *Hallucination-Free?*, 2024 | https://arxiv.org/abs/2405.20362 | `grounded` = 关键事实命题正确引用相关法律文件；`ungrounded` = 关键命题没引用；**`misgrounded` = 引用了但误读来源、或引用了不适用的来源（错误法域／已被推翻）**；`hallucinated` = incorrect ∨ misgrounded。作者明确指出：CS 的 grounded 只看"忠于给定文档"，所以"引对段落但引错法域"在 CS 指标里满分、在法律意义上是幻觉。 |

### 落地建议

**记哪些字段**（每个结论一条 claim 记录）：

```json
"claims": [{
  "claim_id": "c1", "text": "丧假为 1–3 天", "type": "numeric|condition|procedure|boolean",
  "citations": [{
     "doc_no": "沪人社规〔2023〕32号", "clause_path": "第三章/第十二条",
     "span": [10234, 10310], "quote": "职工配偶、父母、配偶父母死亡的，给予丧假一至三天",
     "locatable": "EXACT|DRIFT|NOT_FOUND",
     "sufficient": true, "applicable": true,
     "pad_ratio": 1.2                 // 引用长度 / 最小支撑长度
  }],
  "conclusion_supported": true
}]
```

**算什么数**（四项，前两项机械、后两项半机械）：

| 指标 | 算式 | 目标 | 成本 |
|---|---|---|---|
| `CLR` 引用可定位率 | `#locatable / #citations` | 100%（出现 NOT_FOUND 即 bug） | 纯机械 |
| `AC` 适用率 | `#applicable / #citations`（status=effective ∧ city 匹配 ∧ as_of 在有效期内） | 100% | 纯机械，查效力表 |
| `CSR` 主张支撑率（≈ALCE recall） | `#claims with ≥1 sufficient citation / #claims` | 越高越好 | 数值/条件类可机检（槽位匹配），其余人抽检 |
| `CP` 引用精度（≈ALCE precision） | `#citations (relevant ∧ still-needed) / #citations` | 越高越好 | 人抽检或 NLI |

**为什么不用 RAGAS**：等你们加了 LLM 组装，RAGAS 的 faithfulness 会变成"让 LLM 给自己的引用打分"，循环论证；而且它给不出"哪条引用错了"。要用 LLM，就用 ALCE 的 `φ`（T5-XXL TRUE 或独立 judge），并且必须报 **judge–人 的一致性**（ALCE 的先例是 85.1%/77.6%，低于 80% 就别用）。

**一个必须加的防护**：`pad_ratio`（引用长度 ÷ 最小支撑长度）。"整段照抄"能让 CLR/AC/CSR 全部满分，但 pad_ratio 会飙到 5–20。ALCE §3.4 专门测过这个捷径，你也可以照抄这个思路。建议 pad_ratio > 3 的引用单独计一列。

---

## 4. 引用可验证性的机械判定

### 结论

1. 机械判定**只能证明"这段文字在原文里"，不能证明"这段文字支撑这个结论"**。它挡住的是**编造引用**（不存在的条款、编造的文号、错位的页码），挡不住**断章取义**和**引用正确但推不出**。
2. 所以检查结果必须是**三态**，不是二值：`EXACT`（区间命中）/ `DRIFT`（文档内能找到，但字符偏移不对）/ `NOT_FOUND`。把 DRIFT 和 NOT_FOUND 混成一个"失败"，你会把"PDF 抽页眉导致偏移 37 字"这种 UI bug 当成内容错误，浪费排查时间。
3. 中文政务文档的匹配必须做**归一化**，否则 CLR 永远到不了 100%：全角/半角、`〔〕` vs `（）` vs `[]`（文号里三种都有人用）、空格、换行、OCR 的 `己/已`、`〇/○`、破折号。
4. 机械检查漏掉的四类错误，按危害排序：**失效/错法域（misgrounded）> 断章取义（省掉"但书/除外"）> 引用正确但推不出结论 > 拼接引用**。第一类可以用效力表机械补上，第二类可以用"±1 句扫描"半机械补上，后两类只能靠人称抽检。
5. **你 README 里的 `citation_verifiable 100%` 目前是自证**：如果管线是从索引里直接拷贝片段，那它必然 100%，这是同义反复。必须和 CSR/AC 一起报才有信息量。

### 依据

- Magesh et al. 2024（https://arxiv.org/abs/2405.20362）的 `ungrounded` vs `misgrounded` 二分，正好对应"机械检查能抓"与"机械检查抓不到"两堆错误。三个商用工具在 RAG 加持下仍有 **17%–33%** 的幻觉率，其中相当部分是 misgrounded——**RAG 消除了"编造条文"，没有消除"引错条文"**。
- ALCE（https://arxiv.org/abs/2305.14627）用 NLI 而不是字符串匹配，恰恰是因为字符串匹配无法判断支撑关系；但它的 κ 只有 0.698/0.525，说明连 NLI 都判不准——**机械检查的作用是"必要的下限"，不是"充分的上限"**。
- AIS（https://arxiv.org/abs/2112.12870）要求解读者能说"According to P, s"，强调**去上下文化**：片段单独拿出来是否还成立。断章取义正是"在上下文里成立、单独拿出来不成立"。

### 落地建议

**检查器设计（四级，逐级降级）**

```
0. 归一化：NFKC → 去空白 → 全角标点统一 → 〔〕（）[] 统一 → 数字全角转半角
1. 精确：normalized_doc[start:end] == normalized_quote        → EXACT
2. 容错：在 [start-500, end+500] 窗口内搜索该 quote            → DRIFT（记 offset_delta）
3. 模糊：最长公共子串 / len(quote) ≥ 0.95，且长度 ≥ 15 字      → DRIFT_FUZZY（人工复核）
4. 失败：                                                        → NOT_FOUND（fail CI）
```
配套断言（不要只报比率）：
- `quote` 必须**连续**：禁止用 `…` 拼接两段；检查 quote 内部无省略号且子串命中。
- `len(quote) ≥ 12` 且 `len(quote) ≤ 3 × 最小支撑长度`（否则记 padding）。
- 引用片段必须落在 `clause_path` 指向的条款边界内（跨条款即 fail）——这是"条款路径"字段的唯一价值所在，不检查等于白存。

**补什么**

| 漏掉的错误 | 补充手段 | 成本 |
|---|---|---|
| 失效/错法域 | 效力表硬校验：`status(effective at as_of) ∧ city ∈ applies_to` | 已有字段，1 小时 |
| 断章取义 | 扫描片段前后各 1 句，命中 `除外/不适用/但/本条不/失效/废止/另行规定` 则标 `REVIEW`，人看 | 2 小时，正则 |
| 引用对但推不出 | 结论原子化后做**槽位包含检查**：结论里的数字/条件词必须出现在引用片段中。数字不在片段里 = 直接 fail | 3 小时，覆盖 numeric/condition 两类 |
| 拼接/截断 | 连续性断言 + `must_cite`/`must_not_cite` 金标 | 1 小时 |
| 表格/附件导致的路径错 | 条款路径 + 页码 + 字符区间三者交叉校验，任一不一致标 REVIEW | 2 小时 |

抽检方案：按 `(claim.type × citation.status)` 分层，每层抽 10–15 条人工判 `sufficient`，报**人机一致率**。这一列的长期价值比 CLR 大得多。

---

## 5. 小样本报告：n=50–200 能说什么

### 结论

1. **n=50 只够报"有没有明显的坑"，不够报任何"率"。** p≈0.9 时 ±8.5pp；最坏 p=0.5 时 ±13.4pp。
2. **"100% / 零失败"必须换算成上界**：n=50 零失败 → 真实失败率 ≤ **5.82%**（Clopper-Pearson 单侧 95%），粗略口径 rule of three 是 3/n=6.0%。**n=15 零失败 → ≤18.1%**。所以"15/15 全拒"证明不了什么。
3. **AURC 在小样本下不可比**：n=100 时全 coverage 处风险的标准误，10 个错误 → 3.0pp；20 个错误 → 4.0pp；而每个 10% 的覆盖切片只有 10 个点，1 个错误就是 10%（CI 约 [1.8%, 40%]）。**结论：n=100 只能分辨 >6pp 的 AURC 差；想比两个系统的 AURC，n≥200，且必须用配对 bootstrap。**
4. **引用类指标的有效 n 是 citation 数不是问题数，而且它们聚簇。** 10 个问题 × 3 条引用 = 30 个"样本"，但它们不独立，直接算 CI 会把区间低估约 √3 倍。**必须按问题做 cluster bootstrap。**
5. **"没接入"这一类不要用统计。** 它是索引台账的确定性函数，正确率应当是 100%，出现一次就是 bug。把它从"率"改成"断言"，你就少了一个需要 200 样本才能估准的指标。

### 依据（本机计算的数，可直接引用）

Wilson 95% CI，p=0.90：

| n | k | p | 95% CI | 半宽 |
|---|---|---|---|---|
| 50 | 45 | 0.900 | [0.786, 0.957] | ±8.5pp |
| 100 | 90 | 0.900 | [0.826, 0.945] | ±6.0pp |
| 150 | 135 | 0.900 | [0.842, 0.938] | ±4.8pp |
| 200 | 180 | 0.900 | [0.851, 0.934] | ±4.2pp |
| 300 | 270 | 0.900 | [0.861, 0.929] | ±3.4pp |

最坏情况 p=0.5：n=50 → ±13.4pp；n=100 → ±9.6pp；n=200 → ±6.9pp。

零失败时的失败率上界（单侧 95%）：

| n | rule of three 3/n | 精确 Clopper-Pearson |
|---|---|---|
| 15 | 20.0% | 18.1% |
| 30 | 10.0% | 9.5% |
| 50 | 6.0% | 5.82% |
| 60 | 5.0% | 4.87% |
| 73 | 4.1% | 4.02% |
| 100 | 3.0% | 2.95% |
| 200 | 1.5% | 1.49% |

方法学依据：
- **Wilson 区间**优于正态近似，尤其 p 接近 0/1 和小 n（Brown, Cai & DasGupta, *Interval Estimation for a Binomial Proportion*, Statistical Science 16(2), 2001, https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full）。
- **Clopper-Pearson** 用于 k=0/k=n（保守，正好适合"零失败"声明）。
- **Conformal risk control**（https://arxiv.org/abs/2208.02814）用于操作点：给的是保证不是区间，n=100/α=10% 时容纳 ≤9 个错误。
- **配对 bootstrap / McNemar**：比较两个系统时，绝对不要比两个独立 CI 是否重叠（那是出了名的保守且错误）。用同一批题上的配对差值 bootstrap（B=2000），或对 answer/refuse 决策用 McNemar 精确检验。

### 落地建议

**每个数必须带三样东西**：`k/n`、区间、`n` 属于哪个分层。格式：

```
hit@1  literal     18/20 = 0.900 [0.699, 0.973]
hit@1  paraphrase   1/10 = 0.100 [0.018, 0.404]   ← 别和上一行合成 19/30
CLR    citations   87/88 = 0.989 [0.938, 0.998]   (n_q=41, cluster bootstrap)
```

**样本量规划（写进 EVAL.md）**：

| 要证明的事 | 需要的 n | 理由 |
|---|---|---|
| "拒答理由分类准确率 ≈ 90%" | ≥200（每类 ≥60） | ±4.2pp，三类各 60 才有意义 |
| "引用可定位率 = 100%" | 断言 + ≥100 条引用 | 零失败 → ≤2.95% |
| "AURC 比基线低" | ≥200，配对 | n=100 分辨不了 6pp |
| "paraphrase 比 literal 差" | 现有 10 vs 20 就够（差 80pp） | 效应太大，小样本也显著 |

**bootstrap 实现要点**（3 小时）：
1. 重采样单位是**问题**，不是 claim/citation。
2. 每次重采样重新算整条曲线和 AURC（不要只重采样误差项）。
3. 报 percentile 区间（B=2000 足够；不要 BCa，除非你要发论文）。
4. 比较两系统时用**同一次重采样的索引**（paired），否则区间会宽 2–3 倍。

---

## 6. 反刷分：堵住"全拒"和"全答"

### 结论

1. **"全拒"和"全答"是同一个漏洞的两面：只要指标是单一比率，两边都能刷。** 解法不是加一个更强的指标，是**把两个方向的指标同时挂上去，并且用曲线代替点**。
2. "全拒"的死穴是 **coverage**：它在 risk–coverage 图上是 (0, 0) 这个点，曲线画出来一眼就看穿。所以**必须报曲线或至少报 coverage**，只报"拒答率"等于给它留门。
3. "全答"的死穴是 **`P(answer | gold unanswerable)` + `AC`（适用率）**：它可以靠"引一篇真文档 + 抄一段真原文"把 CLR 刷到 100%，但引的是已失效/别的城市的文件时 `AC` 会掉下来。**这是你们这套语料天然自带的最强对抗集**：报告里已经有一堆 top1 落在 `expired`/`unknown` 的题（如"退休人员养老金调整" → 沪人社规〔2024〕9号 expired），把它们直接变成陷阱用例。
4. 第三种刷法是**钻定义空子**："答了但加一句'仅供参考，建议咨询'"。必须**先定义清楚**：输出里只要出现实质性结论（数字/条件/是/否），就算 `answer`；否则两个策略都能在边界上套利。
5. 用例集配比必须是 **1:1 左右**（可答 : 不可答），否则"全拒"的上限会被配比本身抬高。你现在的 50 条是 18 可解 + 14 不可解 + 12 部分可解 + 6 行为约束，基本合格，但缺"库内有但无政策"这一类（见 §2）。

### 依据

- ALCE §3.4 专门构造了"把 top-1 段落直接当答案并自引"和"整段照抄"两种捷径，指标不虚高——**说明"引真文档"是完全可刷的，必须靠 precision/padding 类指标堵**（https://arxiv.org/abs/2305.14627）。
- AbstentionBench 发现推理微调让弃权率掉 24%，且规模无关（https://arxiv.org/abs/2506.09038）——**"答得更好"和"拒得更准"是两个能力，会互相挤占**，所以两个方向必须分别设门。
- UAEval4RAG 的 Joint Score 用 `0.7·Correctness + 0.3·Acceptable` 把"答对"和"该拒不拒"合成一个数（https://arxiv.org/abs/2412.12300）——这是防止单边刷分的最简做法，但**权重是主观的，我建议你们不要合，直接双门限**。
- Magesh et al. 的 `misgrounded` 是"全答"策略在真实系统上的主要失败形态（17%–33% 幻觉率，RAG 也没消掉）（https://arxiv.org/abs/2405.20362）。

### 落地建议

**针对"全拒"（4 道闸）**

| 闸 | 用例/指标 | 判定 |
|---|---|---|
| 覆盖率下限 | `P(answer \| gold answerable) ≥ 0.70` | CI 门限，低于则 fail |
| 曲线可见 | 必须产出 risk@0.3/0.5/0.7/0.9 四行 | 缺行即 fail（全拒只有 c=0 一个点） |
| 理由可证伪 | 说 `no_policy` 时必须 `in_scope=true`；说 `not_covered` 时台账里必须真没有 | 硬断言，出现即 fail |
| 空转识别 | 拒绝时必须给出 `searched: {n_docs, n_queries}`；`n_queries=0` 的 `no_policy` 判为编造 | 1 小时，字段检查 |

**针对"全答"（4 道闸）**

| 闸 | 用例/指标 | 判定 |
|---|---|---|
| 幻觉应答率上限 | `P(answer \| gold unanswerable) ≤ 0.10` | CI 门限 |
| 适用率 | `AC ≥ 0.98`；`AC` 单列"引用已失效文件"计数 | 硬门限 |
| 数值槽位 | 结论中的数字/阈值必须出现在引用片段内（`slot_in_span`） | 硬断言，出现即 fail |
| 对抗集 | 跨城市同措辞（北京 vs 上海丧假）、已废止版本、`as_of` 双时点同题 | 专用分层，单独报 |

**对抗集清单（照这个造，每条 15–30 分钟）**

1. **跨城市同措辞**：问"北京 丧假 几天"，索引只有上海。BM25 会命中上海文件（词汇高度重叠）→ 考 `not_covered` 识别。
2. **失效版本陷阱**：问"退休人员养老金调整"，top1 是 `expired` 的 2024 年文件 → 考 `AC`。
3. **同题双时点**：`as_of=2024-06` 与 `as_of=2026-06` 答案不同 → 考 `as_of` 是否真被用上。
4. **库内空白**：某市某 topic 确实没发过文 → 考 `no_policy`（这一格你现在完全没有）。
5. **假前提**："我岳父去世，能请几天丧假"——注意"岳父"在某些口径里不算"配偶父母"的直系范围 → 考"结论的条件是否被引用覆盖"。
6. **近似但不适用**：找到"探亲假"文件回答"丧假"问题 → 考 CSR（能找到字，但推不出）。
7. **数字诱饵**：文中有多个数字（3 天/7 天/30 天），结论挑了错的那个 → 考 slot 匹配。

最后一条元规则：**阈值只能在 dev 集上调，test 集每轮重抽 30%。** 你们已有 15 条 held-out，够用，但要保证每次改阈值后重抽。

---

## 最小可行方案：只做 4 件事

按"每小时换来的诚实度"排序。总计约 **31 小时**（≈4 人日）。

| # | 做什么 | 产出 | 难度 |
|---|---|---|---|
| **1** | **三分类输出 + 二维金标 + 五个比率**：输出加 `refusal_reason`、`in_scope`；用例加 `gold.index_has` / `gold.world_has`；建 `eval/scope_manifest.json`；出那张 3×3 表和五个派生比率 | 一张表回答"拒得对不对、理由对不对" | **8h** |
| **2** | **机械引用校验（三态）+ 适用性校验**：归一化 → EXACT/DRIFT/NOT_FOUND；`AC` 查效力表 + 城市；`pad_ratio` | `CLR`、`AC`、`pad_ratio` 三个数，全自动，无 LLM | **5h** |
| **3** | **risk–coverage 曲线 + risk@coverage 表 + Wilson 区间**：用 `top1_norm` 当 `score`，报 4 个 coverage 点 + AURC + 配对 bootstrap | 一张图 + 一张表，替代"拒答率" | **6h** |
| **4** | **补 60 条对抗用例**：20 条"库内空白" + 20 条跨城市/失效陷阱 + 20 条 paraphrase 难例 | 让 §2 的空格和 §6 的对抗集落地 | **12h** |

**为什么是这个顺序**：1 和 2 是**确定性事实**，做完就不会再退步，而且成本最低；3 依赖 1 的标签（没有 `in_scope` 就没有干净的分母）；4 是唯一需要持续投入的，但它决定了另外三件事有没有意义——**现在 paraphrase hit@1 = 1/10，这个 10% 会直接伪装成"拒答率上升"**，不补难例，你会把检索退化误读成模型变谨慎。

**明确不要做**（至少这一轮）：
- 不要建 LLM judge。没有人工一致性基线之前，judge 只是把主观包装成客观。
- 不要报不带区间的 AURC。
- 不要把 literal（n=20）和 paraphrase（n=10）合成一个 30 条的总数。
- 不要给"没接入"算准确率——它是台账查表，该做的是断言。

**做完之后你能说的第一句话**（这也是验收标准）：
> 在 coverage 90% 时，选择性风险为 9/90 = 10.0% [5.3%, 18.0%]；conformal 保证在 α=10% 下该操作点成立。其中"库内空白"类拒答理由准确率 X/Y，假"没接入" 0/Z（硬断言）。引用可定位 88/88 = 100% [96.0%, 100%]，适用率 87/88。

这句话里的每一个数字都能被审计，而"拒答率 30%"不能。
