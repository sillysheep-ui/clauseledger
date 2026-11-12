# 判定型 RAG 的评测指标盘点：拒答、校准、引用与语料覆盖

> 面向中文政务问答 · 文献与链接核验截至 2026-11-12 · 配套 [`ABSTENTION-CITATION-EVAL.md`](ABSTENTION-CITATION-EVAL.md)

这份文档不是“再列一遍 RAG 指标”，而是回答四个会直接影响评测规范的问题：

1. 拒答到底该怎么测，才能同时看见“答错”和“该答不答”？
2. “我们没接入/没检索到”与“世界上确实没有这条政策”能不能分开？现在有没有论文做到？
3. 引用可验证性除了 ALCE 的 recall/precision，还缺什么？“引用真实但文件不适用”有没有专门指标？
4. 中文政务 RAG 有没有公开 benchmark？语料覆盖度有没有被当成一阶指标测过？

**结论先行：**

- **拒答的主指标不是“拒答率”**，而是 `risk@coverage` 曲线 + 分原因拒答 + 可审计的检索证据。单一拒答率会把“该答不答”“不该答硬答”“没接入却编造”混成一个数。
- **关键空白：截至 2026-11-12，没有找到一篇论文把“没接入/没检索到”与“世界上不存在”做成两个独立指标、配 2×2 金标和公开数据。** 最接近的是 UAEval4RAG 的 `Out-of-Database` vs `False-presupposition` 分类，但它仍把两者放在同一个 `Acceptable Ratio` 下按类报告；CRAG 的 `missing` 则把“不知道/找不到/空响应/要求澄清”混在一起。下面第 1.3 节给自建方案。
- **引用可验证性：ALCE 是最低基线，不是完整答案。** 它只判“引用片段是否蕴含这句话”，不判“这条法规是否现行、是否适用这个城市/人群、是否已被废止”。`misgrounded`（引用真实但文件不适用）目前没有专门标量指标；最接近的是 Magesh 等的法律 RAG 审计分类，以及 2026 年 Legal warrant 的“权威五条件”研究议程。
- **中文 RAG 有可用评测，但政务 RAG 引用评测是空白。** CRUD-RAG、DomainRAG、RGB、T2Ranking、C-MTEB 可作为中文检索/生成评测基础；POLIS-Bench、PolicyBench、MSGABench 是公开政务/政策评测，但都不是“开放语料 + 引用可验证性 + 拒答”的 RAG benchmark。CitaLaw 是中文法律引用的直接先例，可借鉴其“三段论”思路，但法条/判例不等于政务政策文件。
- **语料 completeness/coverage 作为一阶指标，几乎没有。** 检索 `recall@k` 依赖 qrels，而 qrels 由 pooling 产生，存在 pooling bias；RAG 的 `context recall` 分母是参考答案的 claim，不是语料库；CRAG 的 `missing` 是答案级标签，不是语料级覆盖。工程上应自建 `source_coverage`、`corpus_recall`、`unjudged_rate`、`bpref`、`coverage_delta`，并明确标注“非论文标准”。
- **所有比例必须带 `k/n` 与区间。** `n<200` 时不要下强结论；零失败不要写“100%”，要报单侧 95% Clopper-Pearson 上界（`1-0.05^(1/n)`，n=50 时约 5.82%）。

**阅读约定：**

- 年份取 arXiv 首版或正式会议年；同一工作跨年时写“2023/2024”。
- 链接在 2026-11-12 通过 arXiv 元数据、论文 HTML、Semantic Scholar/DOI 解析核验；未最终核实的在文中标 `待核`。
- 公式用行内代码表示；`#` 表示计数，`abs()` 表示绝对值，`∣` 表示条件。
- “一句话适用性”写在每张卡片末尾；开头表只做索引，不替代卡片。

---

## 0. 开头表

| 工作 | 年份 | 解决什么 | 核心指标 | 关键数字 | 链接 |
|---|---|---|---|---|---|
| El-Yaniv & Wiener, *On the Foundations of Noise-free Selective Classification* | 2010 | 把“拒答”形式化为 risk–coverage | selective risk `R(f;φ)=R(f)/φ(f)`；coverage `φ=P(f≠⊥)`；AURC | `AURC=∫R(c)dc`；定义论文，无实验 | [JMLR](https://jmlr.csail.mit.edu/papers/v11/el-yaniv10a.html) |
| Geifman & El-Yaniv, *Selective Classification* | 2017 | 用置信度阈值做选择性分类 | `coverage@selective risk`；Softmax Response；SC-SVM | 99.9% 保证 2% 错误时 coverage≈60% | [arXiv](https://arxiv.org/abs/1705.08500) |
| Guo et al., *On Calibration of Modern Neural Networks* | 2017 | 神经网络置信度校准 | `ECE=Σ_m(#B_m/n)·abs(acc(B_m)-conf(B_m))` | 常用 15 bins；温度缩放 | [arXiv](https://arxiv.org/abs/1706.04599) |
| Kamath et al., *Selective QA under Domain Shift* | 2020 | 域偏移下的选择性问答 | `answered@accuracy`；risk–coverage | 80% 准确率下回答 56%，裸 softmax 48% | [arXiv](https://arxiv.org/abs/2006.09462) |
| Angelopoulos et al., *Conformal Risk Control* | 2024 | 有限样本期望风险控制 | `λ̂=inf{λ:(n/(n+1))R̂_n(λ)+1/(n+1)≤α}` | n=100、α=10% 时最多 9 个错误（公式推算） | [arXiv](https://arxiv.org/abs/2208.02814) |
| XSTest | 2023/2024 | 测安全提示上的过度拒答 | `over-refusal rate=#refused safe/#safe` | 250 条 safe/10 类 + 200 条 unsafe | [arXiv](https://arxiv.org/abs/2308.01263) |
| OR-Bench | 2024/2025 | 大规模过度拒答 | `refusal rate on OR prompts` | 80,000 条/10 类/约 1,000 hard/600 toxic/32 LLM | [arXiv](https://arxiv.org/abs/2405.20947) |
| AbstentionBench | 2025 | 通用弃权 benchmark | `abstention rate by category` | 20 数据集/20 LLM；推理微调平均 -24% | [arXiv](https://arxiv.org/abs/2506.09038) |
| SQuAD 2.0 | 2018 | 抽取式不可答 QA | `EM/F1`，no-answer 作为一个答案类 | >50,000 不可答；86%→66% F1 | [arXiv](https://arxiv.org/abs/1806.03822) |
| QuAC | 2018 | 对话中的不可答问题 | `unanswerable rate`；`CANNOTANSWER` 标记 | 100K QA；20.2% 不可答 | [arXiv](https://arxiv.org/abs/1808.07036) |
| CoQA | 2018 | 对话 QA（无可答子集） | `EM/F1`；无 unanswerable 标签 | 127k 问题/8k 对话/7 领域；0 不可答子集 | [arXiv](https://arxiv.org/abs/1808.07042) |
| RGB | 2023/2024 | 中英 RAG 四项能力 | `negative rejection rate`；accuracy | 4 能力/6 LLM；negative rejection 是弱项 | [arXiv](https://arxiv.org/abs/2309.01431) |
| CRAG | 2024 | RAG 真实性四档 | `score∈{1,0.5,0,-1}`；`truthfulness=mean(score)` | 4,409 QA；≤34%/44%/63% | [arXiv](https://arxiv.org/abs/2406.04744) |
| UAEval4RAG | 2024/2025 | RAG 不可答六类 | `Unanswered Ratio`；`Acceptable Ratio`；`Joint=w1·Correctness+w2·Acceptable` | 6 类/27 组合；TriviaQA Acceptable 49.0% | [arXiv](https://arxiv.org/abs/2412.12300) |
| Sufficient Context | 2024 | 上下文充分性与弃权 | `selective accuracy`：回答中正确比例 | 引导弃权 +2–10% | [arXiv](https://arxiv.org/abs/2411.06037) |
| DeclineCraft | 2026 | RAG 弃权，区分“语料/参数没有”与“世界没有” | `CAR=#correct abstain/#unanswerable` | 27 LLM；38/40 配置↑；30/40 配置↓；thinking -14pp | [arXiv](https://arxiv.org/abs/2610.28340) |
| CRUMQs | 2025/2026 | 语料缺失导致的不可答 query | `cheatability rate` | 最多 -81.0% | [arXiv](https://arxiv.org/abs/2510.11956) |
| Sieve and Sage | 2026 | 把检索失败拆成 unanswerable vs distracted | accuracy/Macro-F1 | +69.4pp/+55.2pp/1.99× speedup | [arXiv](https://arxiv.org/abs/2609.35794) |
| SelfAware | 2023 | LLM 自知识/“知道自己不知道” | `self-knowledge F1`（不确定性检测） | 1,032 不可答 + 2,337 可答；20 LLM；GPT-4 75.47% | [arXiv](https://arxiv.org/abs/2305.18153) |
| Chinese SimpleQA | 2024 | 中文事实性 + “未尝试”口径 | `CO/NA/IN/CGA/F-score` | 3,000 题；o1-preview 63.8%、Doubao 61.9% | [arXiv](https://arxiv.org/abs/2411.07140) |
| ALCE | 2023 | 引用召回/精确 | `recall=(1/#S)Σ1[C_i≠∅∧φ(concat(C_i),s_i)=1]`；`precision=mean 1[c relevant]` | 50% 生成缺完整引用支撑；κ .698/.525；85.1/77.6 | [arXiv](https://arxiv.org/abs/2305.14627) |
| RAGAS | 2023/2024 | RAG 忠实性/相关性三元组 | `Faithfulness=\vert V\vert/\vert S\vert`；answer/context relevance | 与人工 pairwise 一致率 0.95/0.78/0.70；不评 citation | [arXiv](https://arxiv.org/abs/2309.15217) |
| Attributed QA | 2022/2023 | Attributed QA 与 AutoAIS | `AIS`；`AutoAIS`（NLI） | Pearson AIS-EM .45；AIS-AutoAIS .96 | [arXiv](https://arxiv.org/abs/2212.08037) |
| AIS | 2021/2023 | 归因的严格定义 | `AIS(s,t,P)=1` iff 泛听众能确认“According to P, s” | 定义论文；不判事实真伪 | [arXiv](https://arxiv.org/abs/2112.12870) |
| AttrScore | 2023 | 归因错误三分类 | 3-class micro-F1 | GPT-4 81–83%；12 领域；错误类型 30.6%/79.4% | [arXiv](https://arxiv.org/abs/2305.06311) |
| Liu et al., *Evaluating Verifiability* | 2023 | 生成式搜索引用审计 | `citation recall/precision`（人工） | 51.5% 句子被引用完全支撑；74.5% 引用支撑其句；4 引擎 | [arXiv](https://arxiv.org/abs/2304.09848) |
| VeriScore | 2024 | claim 级事实性 | `S=Σ support`；`P=S/#C`；`R=min(S/K,1)`；`F1@K` | 8 任务/16 模型；K 取该领域 claim 中位数 | [arXiv](https://arxiv.org/abs/2406.19276) |
| Magesh et al., *Hallucination-Free?* | 2024 | 法律 RAG 的 misgrounded 幻觉 | grounded/ungrounded/misgrounded/hallucinated | 17–33% 时间幻觉；65%/42%；60%+ 查询 incomplete | [arXiv](https://arxiv.org/abs/2405.20362) |
| CitaLaw | 2024 | 中文法律引用 benchmark | 三段论三维：大前提/小前提/结论 | 1,000 题；约 500k 文档；2 开放+7 法律 LLM；κ .7876 | [arXiv](https://arxiv.org/abs/2412.14556) |
| Legal warrant | 2026 | 法律权威“是否适用”的研究议程 | 5 条件：存在/管辖/时效/效力/支撑 | 5 条件；pilot 规模小，指标未标准化 | [arXiv](https://arxiv.org/abs/2609.17546) |
| CRUD-RAG | 2024 | 中文 RAG 四类任务 | `Recall/Precision`；`ROUGE/BLEU/BERTScore/RAGQuestEval` | 300k 新闻→>80k；4 任务 | [arXiv](https://arxiv.org/abs/2401.17043) |
| DomainRAG | 2024 | 中文高校招生领域 RAG | 6 能力分项 | 6 能力；闭卷 LLM 明显吃力 | [arXiv](https://arxiv.org/abs/2406.05654) |
| T2Ranking | 2023 | 中文段落检索 | `nDCG@k`（4 级相关性） | >300k queries/>2M passages | [arXiv](https://arxiv.org/abs/2304.03679) |
| C-MTEB | 2023/2024 | 中文 embedding 评测 | 6 类任务/35 数据集 | 中文 embedding 最高 +10% | [arXiv](https://arxiv.org/abs/2309.07597) |
| DuRetrieval | 2023 | C-MTEB 中文检索子集 | `recall/nDCG` | HF 卡：100,001 corpus/2,000 queries | [HF](https://huggingface.co/datasets/C-MTEB/DuRetrieval) |
| MedBench | 2023/2024 | 中文医疗 benchmark | accuracy | 40,041 题/4 部分 | [arXiv](https://arxiv.org/abs/2312.12806) |
| Huatuo-26M | 2023 | 中文医疗 QA 语料 | retrieval/generation | 26M QA | [arXiv](https://arxiv.org/abs/2305.01526) |
| POLIS-Bench | 2025 | 双语政务政策任务 | `semantic similarity + accuracy` | 3,000 题（1,000×3）/3 任务/>10 LLM | [arXiv](https://arxiv.org/abs/2511.04705) |
| PolicyBench/PolicyLLM | 2026 | 美中政策理解 | 3 层 accuracy：记忆/理解/应用 | 21K cases | [arXiv](https://arxiv.org/abs/2604.12995) |
| MSGABench | 2025 | 中文政务 LLM 评测 | accuracy/reliability | 15 LLM；部分 <35%；GPT-4 >95% | [DOI](https://doi.org/10.1145/3716854) |
| Zobel, *How Reliable Are IR Experiments?* | 1998 | pooling bias | recall 偏差 | 无数字；SIGIR | [DOI](https://doi.org/10.1145/290941.291014) |
| Buckley & Voorhees, *Retrieval Evaluation with Incomplete Information* | 2004 | 不完整判断下的评测 | `bpref` | 无数字；SIGIR | [DOI](https://doi.org/10.1145/1008992.1009000) |

**一句话适用性速查（按“能不能直接抄”分组）：**

- **可直接抄的指标/协议：** El-Yaniv 2010（risk–coverage/AURC）、Guo 2017（ECE）、Kamath 2020（answered@accuracy）、Angelopoulos 2024（CRC）、ALCE（citation recall/precision）、AIS/Attributed QA（归因定义与 AutoAIS）、AttrScore（三分类）、CitaLaw（中文法律三段论）、T2Ranking/C-MTEB（中文检索）、Zobel/bpref（qrels 不完整时的告警）。
- **可抄但必须改造：** XSTest/OR-Bench（只测安全过度拒答，不是 RAG 不可答）、AbstentionBench（通用弃权，缺“语料/世界”二维）、CRAG（`missing` 混因）、UAEval4RAG（六类可借，但无 2×2）、Sufficient Context（上下文不足 ≠ 世界不存在）、DeclineCraft（最接近“语料没有但世界有”）、CRUMQs（语料缺失造题）、Sieve and Sage（区分 unanswerable/distracted）、SelfAware/Chinese SimpleQA（自知识/NA，不是检索拒答）、VeriScore（claim 级，不是 citation 级）、Magesh（misgrounded 分类，不是标量）、Legal warrant（五条件，是议程）、POLIS-Bench/MSGABench/PolicyBench（政务任务，不是 RAG 引用评测）、MedBench/Huatuo（医疗领域，不是引用评测）。
- **不要直接用：** 把 SQuAD 2.0 的 EM/F1 当拒答指标；把 QuAC/CoQA 当中文政务不可答集；把 RGB 的 negative rejection 当引用指标；把 RAGAS 当 citation attribution 指标；把 DuRetrieval 当政务检索集；把 XSTest/OR-Bench 的 over-refusal 当 RAG 弃答。

---

## 1. 拒答与选择性预测

### 1.1 经典框架：先把“拒答”写成 risk–coverage

选择性预测（selective prediction）把模型输出扩展为 `Y ∪ {⊥}`，`⊥` 表示拒答。设 `f` 为模型，`φ(f)=P(f(x)≠⊥)` 为 coverage，`R(f)=E[ℓ(f(x),y)]` 为全体样本上的风险，则：

```text
selective risk  R(f;φ) = R(f) / φ(f)
coverage        φ(f)   = P(f(x) ≠ ⊥)
```

给定一个置信度/风险打分 `s(x)`，按阈值 `τ` 拒绝 `s(x)<τ` 的样本，就得到一条 risk–coverage 曲线：

```text
coverage c(τ)      = P(s(x) ≥ τ)
selective risk R(c)= E[ℓ(f(x),y) ∣ s(x) ≥ τ]
AURC               = ∫_0^1 R(c) dc
E-AURC             = AURC - AURC_oracle
```

`AURC` 越低越好；`E-AURC` 是相对“最优拒答器”的 excess。最优拒答器是对后验风险 `ρ(x)=1-max_y P(y∣x)` 做阈值化：`ρ(x)>t` 则拒答。这个框架的价值在于：**它强迫你同时报 coverage 和 risk**。只报 accuracy 的系统，可以通过“多拒答”把 accuracy 做高；只报拒答率的系统，可以通过“多硬答”把拒答率做低。两者都不告诉你用户实际拿到了多少正确答案。

**最小报告集：**

| 指标 | 公式/口径 | 回答的问题 |
|---|---|---|
| `risk@coverage` | 在 coverage=`c` 时，已回答样本上的错误率 | 用户实际拿到的答案有多可靠 |
| `AURC` | `∫R(c)dc`（梯形积分） | 整条曲线的汇总 |
| `E-AURC` | `AURC-AURC_oracle` | 离最优拒答器多远 |
| `ECE` | `Σ_m(#B_m/n)·abs(acc(B_m)-conf(B_m))` | 置信度能不能当闸门 |
| `Brier` | `(1/n)Σ(p_i-y_i)^2` | 概率预测的均方误差 |
| `coverage` | `#answered/#all` | 系统实际敢答多少 |

**经典结果与适用性：**

- **El-Yaniv & Wiener 2010**（*On the Foundations of Noise-free Selective Classification*，JMLR 11:1605–1641）：定义 selective risk、coverage、risk–coverage、AURC；证明最优拒答器是对后验风险阈值化。→ **可直接抄**：这是我们所有拒答指标的母语；但它是二分类框架，多分类/生成式要定义 `ℓ`（EM、F1、claim 级错误、人工 0/1）。
- **Geifman & El-Yaniv 2017**（arXiv:1705.08500）：Selective Classification；Softmax Response 是强基线；在 ImageNet top-5 上，以 99.9% 概率保证 2% 错误率时 coverage 约 60%。→ **可抄**：用“给定 risk 目标，报 coverage”的表述；但 softmax 在 LLM 上通常过自信，不能直接当置信度。
- **Kamath et al. 2020**（arXiv:2006.09462）：Selective QA under Domain Shift；在 80% 准确率目标下，校准后的系统回答 56%，裸 softmax 只回答 48%；OOD 需要训练校准器。→ **可抄**：域偏移（中文政务的城市/年份切片）必须单独校准，不能拿 ID 阈值直接用。
- **Angelopoulos et al. 2024**（arXiv:2208.02814）：Conformal Risk Control；`λ̂=inf{λ:(n/(n+1))R̂_n(λ)+1/(n+1)≤α}`；n=100、α=10% 时，覆盖集内最多 9 个错误（公式推算）。→ **可抄**：如果要对“错误率≤α”给有限样本保证，用 CRC；但它要求可交换性，城市/年份分层后样本太少，保证会变松。它是“风险保证”，不是“每层都达标”的替代品。
- **Guo et al. 2017**（arXiv:1706.04599）：ECE；常用 15 个 bin；温度缩放。→ **可抄**：ECE 是门槛指标，不是装饰；必须同时画 reliability diagram，并报分层的 ECE（按城市、问题类型、文档类型）。ECE 对 bin 数和样本量敏感，不要只报一个总数。

**常见误用：**

- 在 test 上调拒答阈值，然后报 test 的 risk–coverage。这是把测试集当验证集，曲线会偏乐观；阈值只能在 dev 上定，test 只跑一次。
- 只报 `AURC` 不报 `risk@coverage`。AURC 是积分，会掩盖“高 coverage 区间突然崩”的问题；政务场景更关心 `coverage=0.7/0.9` 时的风险。
- 把 `ECE=0.02` 当成“校准很好”。ECE 是平均，可能在少数高风险切片上严重失准；必须报 `ECE_max_slice` 和样本量。

---

### 1.2 拒答 benchmark 地图：它们各自在测什么

| 工作 | 不可答/拒答构造 | 指标（公式级） | 关键数字 | 适用性 |
|---|---|---|---|---|
| XSTest | 250 条“看起来不安全但其实安全”的 prompt + 200 条 unsafe 对照 | `over-refusal rate=#refused safe/#safe`；unsafe 上测 full compliance | 250/200；10 类 safe | 只测安全过度拒答；**不是** RAG 不可答 |
| OR-Bench | 80,000 条 over-refusal prompts，10 类；约 1,000 hard、600 toxic | `refusal rate`；按类分解 | 80k/10 类/1k hard/600 toxic/32 LLM | 大规模安全 over-refusal；**不是**知识边界 |
| AbstentionBench | unknown answers、underspecification、false premises、subjective、outdated | `abstention rate by category`；answerable 上测 accuracy | 20 数据集/20 LLM；推理微调平均 -24% | 通用弃权；可借分类，缺“语料/世界”二维 |
| SQuAD 2.0 | >50,000 条对抗式 unanswerable | 官方 `EM/F1`；no-answer 作为一个答案类 | 强模型 86%→66% F1 | 抽取式不可答；不是 RAG 引用/拒答 |
| QuAC | 对话中的不可答问题；`CANNOTANSWER` | `unanswerable rate`；答案 F1 | 100K QA；20.2% 不可答 | 对话不可答；不是中文政务 |
| CoQA | 全部可答；**没有** unanswerable 子集 | `EM/F1`；无拒答标签 | 127k 问题/8k 对话/7 领域 | 不要把它当不可答 benchmark |
| RGB | 中英 RAG；negative rejection testbed | `negative rejection rate`；accuracy | 4 能力/6 LLM；negative rejection 是弱项 | 中文 RAG 拒答参考；不是引用指标 |
| CRAG | 4,409 QA；perfect/acceptable/missing/incorrect | `score∈{1,0.5,0,-1}`；`truthfulness=mean(score)` | ≤34%/44%/63% | RAG 真实性；`missing` 混因 |
| UAEval4RAG | 六类：Underspecified/False-presupposition/Nonsensical/Modality-limited/Safety/Out-of-Database | `Unanswered Ratio`；`Acceptable Ratio`；`Joint=w1·Correctness+w2·Acceptable` | 6 类/27 组合；TriviaQA Acceptable 49.0% | 最接近“没接入 vs 不存在”的分类学 |
| Sufficient Context | 上下文是否足以回答 | `selective accuracy`：回答中正确比例 | 引导弃权 +2–10% | 区分“上下文不足”与“模型不用”；不是“世界不存在” |
| DeclineCraft | 语料 cutoff 后的 Minecraft 特性：世界上有、语料/参数里没有 | `CAR=#correct abstain/#unanswerable` | 27 LLM；38/40 配置↑；30/40 配置↓；thinking -14pp | 最接近“语料没有 ≠ 世界没有” |
| CRUMQs | 自动构造 uncheatable、realistic、unanswerable、multi-hop query | `cheatability rate` | 最多 -81.0% | 造“语料缺失”题；不是原因标签 |
| Sieve and Sage | unanswerable state（证据缺失）vs distracted state（证据冲突/否定/对抗） | accuracy/Macro-F1 | +69.4pp/+55.2pp/1.99× | 检索失败诊断；不是“世界不存在” |
| SelfAware | 1,032 不可答 + 2,337 可答；5 类 | `self-knowledge F1`（不确定性检测） | 20 LLM；GPT-4 75.47% | 参数化自知识；不是检索拒答 |
| Chinese SimpleQA | 3,000 中文事实题；有“未尝试”口径 | `CO/NA/IN/CGA/F-score` | 63.8%/61.9% | 中文事实性 + NA；不是 RAG 引用 |

**逐项判断：**

- **XSTest / OR-Bench：** 它们测的是 safety over-refusal——“用户问的是正常问题，模型因为看起来敏感而拒答”。这跟“知识库里没有这条政策”是两种构念。把它们混进 RAG 拒答率，会得到一个既不受安全团队认可、也不受知识团队认可的数。**结论：只能作为安全侧补充，不能作为 RAG 弃答主指标。**
- **AbstentionBench：** 目前最系统的通用弃权 benchmark：20 个数据集、20 个前沿 LLM，覆盖 unknown/underspecified/false premise/subjective/outdated；关键发现是 reasoning fine-tuning 平均把弃权率打掉 24%，规模几乎不帮忙。→ 可借它的“按不可答类型分解”做法；但它没有 `index_has × world_has` 金标，也没有检索证据字段。
- **SQuAD 2.0：** 官方 EM/F1 把 no-answer 当成一个答案类，模型可以永远输出 no-answer 来刷 F1 的一部分；它不区分“没检索到”和“世界上没有”。→ 只能作为“抽取式不可答”的历史基线。
- **QuAC：** 20.2% 不可答，有 `CANNOTANSWER` 标记，适合研究对话上下文下的弃权；但不是中文政务，也没有引用。
- **CoQA：** 官方数据全部可答，没有 unanswerable split。**不要虚构它的不可答子集**；如果要做对话拒答，自己构造并标注。
- **RGB：** 中英双语 RAG 的四能力（noise robustness、negative rejection、information integration、counterfactual robustness），6 个 LLM；negative rejection 是显著弱项。→ 中文 RAG 拒答的合适参考，但它的指标是 answer accuracy / rejection rate，不是 citation recall/precision。
- **CRAG：** 4,409 QA、5 领域、8 题型；标注 perfect/acceptable/missing/incorrect，分数 1/0.5/0/-1；`missing` 的定义包括“I don't know”“I'm sorry I can't find…”、空响应、要求澄清。`truthfulness` 是平均分。关键数字：LLM ≤34% accuracy，加 RAG 44%，工业 SOTA 63% 无幻觉。→ **可抄它的“missing 比 incorrect 轻” Scoring**（missing 0，incorrect -1），但 **不能抄它的 missing 当拒答原因**：它把“找不到”“不愿答”“要澄清”混成一类。
- **UAEval4RAG：** 六类不可答；27 种组件组合；TriviaQA 行 Acceptable 49.0%，分项 Under 24.0 / False-presupposition 87.0 / Nonsensical 51.0 / Modality-limited 10.0 / Safety 46.0 / Out-of-Database 76.0；Unanswered 30.0，Clarification 15.8，Joint 76.58。→ **最接近“没接入 vs 不存在”的拆分**：`Out-of-Database` 对应“库外”，`False-presupposition` 对应“前提不成立/世界不是这样”。但它仍然把两者放在同一个 `Acceptable Ratio` 下按类报告，没有 2×2 交叉，也没有“假没接入率/假查过没有率”。它是**分类学参考**，不是我们要的最终指标。
- **Sufficient Context：** 定义 context sufficiency；大模型在 context 不足时倾向硬答，小模型在 context 充足时也常幻觉/弃权；用 sufficiency 引导弃权，使“回答中正确比例”提升 2–10%。→ 它区分的是“检索上下文不足”与“模型不会用”，不是“世界不存在”。可用于诊断 reader，但不能替代语料覆盖指标。
- **DeclineCraft：** 用 corpus cutoff 后的 Minecraft 特性造“世界上有、语料/参数里没有”的题；指标 CAR；27 个 LLM；single-shot retrieval 在 38/40 配置提高 CAR，iterative RAG 在 30/40 配置降低 CAR，thinking 在 8/13 模型降低 CAR 平均 14pp。→ **最接近我们要的“语料没有 ≠ 世界没有”构念**；但它只有“没有”这一侧，没有 `no_policy`（库里有、世界无规定）这一侧，也没有 2×2。
- **CRUMQs：** 自动构造 uncheatable、realistic、unanswerable、multi-hop query，针对 corpus 缺失/不完整；cheatability 最多降 81.0%。→ 适合造“检索池缺失”的测试题；不提供拒答原因标签。
- **Sieve and Sage：** 把检索失败拆成 `unanswerable state`（所需证据缺失）与 `distracted state`（相关证据混有冲突/否定/对抗信息）；Sieve 过滤 + Sage 生成/弃权；accuracy 最多 +69.4pp、Macro-F1 +55.2pp、1.99× speedup。→ 适合诊断“检索到了但被带偏”，不是“世界不存在”。
- **SelfAware：** 1,032 不可答 + 2,337 可答，5 类；20 个 LLM；GPT-4 self-knowledge 75.47%，仍低于人类。→ 参数化自知识的标准参考；不能直接用于 RAG，因为“模型不知道”不等于“语料没有”。
- **Chinese SimpleQA：** 3,000 题、6 主题/99 子主题；指标 CO/NA/IN/CGA/F-score；o1-preview 63.8%、Doubao-pro-32k 61.9%。`NA`（not attempted）是中文事实性评测里少见的“未尝试”口径，但它不等于“拒答”，也不带检索证据。→ 可作为中文事实性/校准的补充，不能当 RAG 拒答主指标。

---

### 1.3 关键：把“没接入/没检索到”与“世界上不存在”分开

#### 1.3.1 三个概念，四种结局

先把“不知道”拆成可操作的状态：

- `index_has`：在**当前语料快照**里，是否存在能回答该问题的权威文档/条款（可由 `gold_doc_ids` 判断）。
- `world_has`：在**现实世界**里，是否存在权威规定/事实（由人工对照官网、法规库、政策原文核实，不依赖当前语料）。
- `in_scope`：问题是否属于系统职责范围（例如“只答本市政策”，问外省就是 out of scope）。

由此得到 2×2：

| | `world_has=true` | `world_has=false` |
|---|---|---|
| `index_has=true` | **ANSWERABLE**：正常回答 + 引用 | **NO_POLICY**：查过权威文本，文本确实没有规定；可以答“现行政策未规定”，并给出查过的文件 |
| `index_has=false` | **NOT_COVERED**：必须承认“我们没接入/没检索到”，不能说“世界上没有” | **NOT_COVERED**：同样只能承认“未接入，无法确认”；**不能说“查过了，没有”** |

四个格子的正确话术：

1. **ANSWERABLE**：“根据《X 办法》第 N 条，……”+ 引用 + 生效时间。
2. **NO_POLICY**：“我们检索了现行有效的《X 办法》《Y 通知》，其中没有对 Z 的规定；以下是我们查到的相邻条款……”+ 检索范围。
3. **NOT_COVERED（库无、世界有）**：“我们暂未接入 A 市实施细则，无法确认 Z 的规定；已接入范围是……”——这是**能力边界**，不是“世界没有”。
4. **NOT_COVERED（库无、世界无）**：“我们未接入相关文件，无法确认 Z 是否存在规定。”——仍然不能说“查过没有”。

`out_of_scope` 是第五种结局：问题不属于职责范围，既不是 `not_covered`，也不是 `no_policy`；要单独标，否则会把“不归我们管”混进“没接入”。

`clarify` 不是拒答：问题是歧义的（“这个补贴”指哪个补贴），系统要求澄清；它应该和“拒答”分开统计，否则会把“交互失败”算成“知识边界”。

#### 1.3.2 为什么必须分开

- **补救动作不同。** `NOT_COVERED` 的修复是“接入文件/修检索”；`NO_POLICY` 的修复是“把‘无规定’的答案写清楚，并给查过的文件”；两者混在一起，产品不知道该补语料还是补话术。
- **法律/舆情风险不同。** 把“没接入”说成“世界上没有”，是在对一个权威事实做虚假陈述；把“政策没规定”说成“没接入”，是白白放弃一个可回答的高价值问题。
- **指标会互相抵消。** 一个系统可以对 `NOT_COVERED` 很诚实（大量说“没接入”），但对 `NO_POLICY` 很糟（把“有规定”说成“没规定”）；单一拒答率会把这两种相反的错误平均掉。

#### 1.3.3 现有论文做到哪一步：有分类，没有独立指标

截至 2026-11-12 的检索，**没有找到一篇论文同时做到**：

1. 把 `index_has` 与 `world_has` 作为两个独立维度标注；
2. 为“假没接入率”“假查过没有率”分别定义分母和公式；
3. 给出公开数据与可复现的评测协议。

最接近的工作，以及它们差在哪：

- **UAEval4RAG（最近）**：`Out-of-Database` vs `False-presupposition` 在分类学上最接近“库外”vs“世界不成立”。但它的指标是每个类别的 `Acceptable Ratio` / `Unanswered Ratio`，不是 2×2 交叉；它没有把“库外但世界有”与“库外且世界无”分开，也没有“假没接入率”。**结论：可借分类，不可借指标。**
- **CRAG**：`missing` 包含“I don't know”“can't find”“空响应”“要求澄清”；它把“没检索到”“不愿答”“要澄清”混为一类，且 `missing` 得 0 分、`incorrect` 得 -1 分。**结论：可借“missing 比 incorrect 轻”的评分哲学，不能借它的原因标签。**
- **Sufficient Context**：区分“上下文不足”与“模型不会用”，但它的“不足”是相对当前检索上下文，不是相对世界；它不处理 `NO_POLICY`。
- **DeclineCraft**：明确构造“世界上有、语料/参数里没有”的题（corpus cutoff 后的 Minecraft 特性），是“语料没有 ≠ 世界没有”的最佳实证；但它只有 `NOT_COVERED` 一侧，没有 `NO_POLICY` 一侧，也没有 2×2。
- **CRUMQs**：用 corpus 缺失造不可答题，测 cheatability；它关心“题能不能被语料作弊”，不关心拒答原因。
- **Sieve and Sage**：区分“证据缺失”与“证据冲突/否定/对抗”，是检索失败诊断；不是“世界不存在”。
- **Legal warrant（2026）**：提出法律权威的五条件——权威存在、适用于相关管辖、在分析时点有效、法律效力状态被正确表示、支持该命题；还要求混合响应策略（回答/限缩/追问/警告/纠正错误前提/弃权）。这是目前最接近“引用真实但文件不适用”的框架，但它是 position paper，pilot 很小，指标未标准化。**结论：可借五条件做 `Applicable` 层，不能宣称它是现成指标。**

**明确结论：** “没接入/没检索到”与“世界上不存在”在文献里**有分类学上的影子，但没有成为两个独立指标**。我们要自己建 2×2 金标与分母；这是本盘点最重要的空白，也是作品仓库里最值得写的“评测设计”部分。

#### 1.3.4 自建方案：可直接写进评测规范

**（1）金标字段。** 每题标注：

```json
{
  "question_id": "...",
  "in_scope": true,
  "index_has": false,
  "world_has": true,
  "gold_doc_ids": ["..."],
  "gold_answer": "...",
  "as_of": "2026-11-12",
  "region": "A市",
  "annotator": "...",
  "evidence_url": "..."
}
```

`index_has` 必须绑定 `corpus_snapshot`；`world_has` 必须由人工对照官网/法规库核实，并记录 `evidence_url` 与 `as_of`。不要让模型自己判 `world_has`——它会把“我没见过”当成“世界没有”。

**（2）输出标签。** 每个回答标一个：

```text
answer
refuse_not_covered
refuse_no_policy
refuse_out_of_scope
clarify
```

**（3）核心指标。** 分母要写清楚：

```text
coverage_by_cell(cell) = #pred=answer / #gold_cell
false_not_covered_rate = P(pred=refuse_not_covered | gold.in_scope=true ∧ gold.index_has=true)
false_no_policy_rate   = P(pred=refuse_no_policy   | gold.in_scope=true ∧ ¬(gold.index_has=true ∧ gold.world_has=false))
fabricated_no_policy   = P(pred=refuse_no_policy   | searched.n_docs=0)
answer_on_missing_rate = P(pred=answer             | gold.index_has=false)
reason_macro_F1        = macro-F1 over {answer, not_covered, no_policy, out_of_scope, clarify}
```

**硬断言（不是“越低越好”的软指标）：**

- `false_not_covered_rate = 0`：语料里明明有，系统却说“没接入”，这是检索/阅读失败，不是能力边界。
- `false_no_policy_rate = 0`：只有“库里有文件、文件确实没规定”这一格允许说 `no_policy`；世界上明明有规定，或库里根本没有文件，都不能说“查过，没有”。
- `fabricated_no_policy = 0`：`n_docs=0` 时不允许说“查过了”；没有检索证据的 `no_policy` 一律判编造。
- `answer_on_missing_rate = 0`：`index_has=false` 时不允许给确定性答案；要么 `not_covered`，要么明确说“无法确认”。

这四条应该作为**发布门禁**，不参与平均分。只要有一条 >0，就说明系统在某一类上说了假话；平均分再高也不能发布。

**（4）`searched` 证据字段。** 每次拒答/`no_policy` 必须带：

```json
{
  "n_docs": 12,
  "n_queries": 3,
  "top_k": 20,
  "index_version": "cv-20261111-1",
  "corpus_snapshot": "sha256:...",
  "gold_doc_hit": false,
  "retrieval_miss": true,
  "latency_ms": 840
}
```

规则：

- `refuse_no_policy` 要求 `n_docs>0` 且 `n_queries>0`；否则判 `fabricated_no_policy`。
- `refuse_not_covered` 要再分 `no_source`（语料没有）与 `retrieval_miss`（语料有、没检索到）；两者的修复动作不同。
- `n_queries=0` 的 `not_covered` 也是无效的：没查就说没接入，和“查过没有”一样是编造。
- `searched` 要能对上 `corpus_snapshot`；跨快照比较时，必须同时报两个快照的覆盖率。

**（5）诊断指标：把“没检索到”再拆一层。**

```text
retrieval_miss_rate = #(gold.index_has=true ∧ gold_doc ∉ top_k) / #(gold.index_has=true)
reader_miss_rate    = #(gold_doc ∈ top_k ∧ pred≠answer) / #(gold_doc ∈ top_k)
```

- `retrieval_miss_rate` 高 → 修检索/切块/索引；
- `reader_miss_rate` 高 → 修 prompt/阅读器/引用对齐；
- `no_source` 高 → 修语料接入（`source_coverage`）；
- 三者都不高但 `false_no_policy` 高 → 修“无规定”话术与检索范围说明。

**（6）中文政务的例子（用我们自己的 313/337）。**

假设入口横幅写“已接入 313/337 个城市（含 4 个直辖市；语料版本 cv-20261111-1），24 个未接入”：

- 用户问“A 市（未接入）的灵活就业补贴条件”：正确结局是 `NOT_COVERED`，话术必须承认“未接入 A 市实施细则”，不能说“A 市没有这个补贴”。如果系统说 `NO_POLICY`，就是 `fabricated_no_policy`；如果系统给答案，就是 `answer_on_missing`。
- 用户问“B 市（已接入）的某补贴”，语料里有 B 市实施细则，但检索没召回：正确结局仍是 `NOT_COVERED`，但要标 `retrieval_miss=true`；如果系统说 `NO_POLICY`，就是 `false_no_policy`（它明明有文件却没查到）。
- 用户问“B 市对某新型业态有没有规定”，语料里有 B 市文件，但文件确实没写：正确结局是 `NO_POLICY`，并列出查过的文件；如果系统说 `NOT_COVERED`，就是 `false_not_covered`（它把“查过没有”说成了“没接入”，白丢一个可回答的问题）。

这就是为什么“拒答率”没有意义：上面三种情况都可能是“拒答”，但一个诚实、一个检索失败、一个虚假陈述。

---

### 1.4 LLM 弃权与“知道自己不知道”

这部分研究的是**参数化知识**下的弃权，不是检索覆盖。可以借它的测量方法，但不能把它当 RAG 拒答的替代品。

- **Kadavath et al. 2022**（[arXiv:2207.05221](https://arxiv.org/abs/2207.05221)）：让模型自评“这个答案对不对”得到 `P(True)`，自评“我知不知道”得到 `P(IK)`；在合适格式下，大模型的校准较好，`P(IK)` 跨任务有部分泛化。→ **可抄**：把 `P(True)`/`P(IK)` 作为**辅助置信度**，与检索分数、引用覆盖一起做门控；不要单独用它决定是否拒答。
- **SelfAware 2023**（arXiv:2305.18153）：1,032 条不可答 + 2,337 条可答，5 类；20 个 LLM；用不确定性检测的 F1 衡量 self-knowledge；GPT-4 75.47%，仍低于人类。→ **可抄**：用“不可答/可答配对”测自知识；但它的不可答是“百科上没有”，不是“我们的语料没有”。
- **Just Ask for Calibration 2023**（[arXiv:2305.14975](https://arxiv.org/abs/2305.14975)）：RLHF-LLM 的 verbalized confidence 通常比 conditional probability 校准更好；在 TriviaQA/SciQ/TruthfulQA 上 ECE 相对降约 50%。→ **可抄**：同时收“口述置信度”和“序列 logprob”，在 dev 上比较谁更校准；不要假设 logprob 一定更好。
- **Can LLMs Express Their Uncertainty? 2023/2024**（[arXiv:2306.13063](https://arxiv.org/abs/2306.13063)）：黑盒 confidence elicitation；LLM 口述置信度普遍过度自信；规模提升校准/失败预测。→ **可抄**：把“置信度”当待校准信号，不要当真实概率。
- **Semantic Uncertainty 2023**（[arXiv:2302.09664](https://arxiv.org/abs/2302.09664)）：对语义等价输出聚类后算 semantic entropy；比 baseline 更预测 QA 正确性。→ **可抄**：生成式回答可以用 semantic entropy 做“是否该弃答”的信号；但它需要多次采样，成本高，且不解决“语料没有”。
- **Chinese SimpleQA 2024**（arXiv:2411.07140）：3,000 题、6 主题/99 子主题；指标 `CO/NA/IN/CGA/F-score`；`NA` 是“未尝试”，不是“明确拒答”。→ **可抄**：中文评测要单列 `NA`，不要把“没答”直接算错；但要区分 `NA`（没尝试/没答全）与 `refuse`（明确说不知道）。

**LLM 弃权的常用指标：**

```text
abstention_rate      = #refuse / #unanswerable
over_answer_rate     = #answer  / #unanswerable
selective_accuracy   = #correct / #answered
failure_prediction   = AUROC(confidence, correctness)
self_knowledge_F1    = 2PR/(P+R) over “不可答/可答”二分类
ECE / Brier          = 置信度校准
```

**关键警告：** 这些指标回答的是“模型知不知道自己不知道”，不是“我们的语料有没有”。一个模型可以很诚实地承认“我不知道”，但这是因为检索没召回；也可以很自信地答对，但引用是错的。**RAG 拒答必须把模型自评、检索证据、语料覆盖三者分开。**

---

### 1.5 对中文政务的适用性判断

**直接可用：**

- `risk@coverage`（0.3/0.5/0.7/0.9）+ `AURC/E-AURC` + `ECE/Brier`：作为主报告框架。
- 2×2 金标 + 四条硬断言：作为发布门禁。
- `searched` 字段 + `retrieval_miss_rate/reader_miss_rate`：作为诊断。
- 分类型弃权率（政策类型、城市、问法）：作为切片分析。

**可借但要改造：**

- CRAG 的“missing 0 分、incorrect -1 分”：借评分哲学，不借 `missing` 标签。
- UAEval4RAG 的六类：借 `Out-of-Database`/`False-presupposition` 的区分，补 2×2 和独立分母。
- DeclineCraft 的 CAR：借“语料 cutoff 后”的造题方法，补 `NO_POLICY` 一侧。
- AbstentionBench 的分类：借 unknown/underspecified/false premise/outdated，补“语料/世界”维度。

**不要用：**

- XSTest/OR-Bench 的 over-refusal rate 作为 RAG 弃答主指标。
- SQuAD 2.0 的 EM/F1 作为拒答指标。
- CoQA 的“不可答子集”（不存在）。
- 单一“拒答率”作为发布指标。

**建议的最小评测集（每个城市/每类政策至少）：**

| 金标格子 | 最少题数 | 目的 |
|---|---|---|
| ANSWERABLE | 200 | 测正常回答与引用 |
| NOT_COVERED（库无、世界有） | 100 | 测“承认没接入” |
| NOT_COVERED（库无、世界无） | 50 | 测“不编造查过” |
| NO_POLICY（库有、世界无规定） | 100 | 测“查过确实没有” |
| OUT_OF_SCOPE | 50 | 测职责边界 |
| CLARIFY | 50 | 测歧义处理 |

n=50 时，零失败的 95% 单侧 Clopper-Pearson 上界约 5.82%；n=100 约 2.95%。所以“零失败”不等于“不会失败”，报告必须带上界。

## 2. 引用可验证性

### 2.1 先定义“可验证”：四层，不是“有链接”

一个引用 `c=(doc_id, span, url)` 支撑一个原子主张 `s`，要逐层过：

| 层 | 名称 | 判定 | 自动化 | 失败长什么样 |
|---|---|---|---|---|
| L1 | **Locatable 可定位** | `span` 在 `doc_id` 的原文中逐字存在，字符区间可回查 | 精确匹配/偏移校验 | 链接 404、引文被改写、页码错、拼接了不存在的话 |
| L2 | **Sufficient 能支撑** | 把 `span` 单独给一个读者，能推出 `s`（不需要外部知识） | NLI/LLM judge + 人工抽检 | 引了相邻条款、引了总则、引了不相关的处罚决定 |
| L3 | **Applicable 适用** | `span` 在 `as_of` 时点有效，且管辖区域、主体、事项、效力层级匹配 | 元数据规则 + 人工 | 引了已废止版本、引了外省文件、引了旧法、引了不适用的人群 |
| L4 | **Conclusion-level supported 结论级支撑** | 回答中每个规范性命中至少有一条 L1+L2+L3 的引用；没有“无引用结论” | 逐 claim 聚合 | 每个引用都真，但结论靠模型自己编；或引 A 条款、结论来自 B |

**四个核心指标：**

```text
CLR  = #locatable citations / #citations                 （L1 命中率）
CSR  = #claims with ≥1 sufficient citation / #claims     （L2 覆盖，≈ALCE recall）
AC   = #applicable citations / #citations                （L3 命中率）
CP   = #citations(relevant ∧ still-needed) / #citations  （L2 精确率，ALCE 口径）
pad_ratio = citation_span_length / minimal_sufficient_span_length
```

`CLR` 低是工程问题（链接/偏移/解析），`CSR` 低是生成问题（该引没引/引了但不支撑），`AC` 低是法律/政策问题（引错版本/法域），`CP` 低是冗余/堆引用。四个都报，才能定位。

**结论级还有一个更直观的指标：**

```text
unsupported_claim_rate = #claims without any L1+L2+L3 citation / #normative claims
```

它和 `CSR` 的分母不同：`CSR` 看“有引用的 claim 里有多少被支撑”，`unsupported_claim_rate` 看“所有规范 claim 里有多少完全没被支撑”。政务回答里后者更危险——一段话可能只给一个总结句加了引用，其余具体条件都没引。

---

### 2.2 ALCE：citation recall / precision 的事实标准

**论文：** Enabling Large Language Models to Generate Text with Citations（ALCE），2023/EMNLP 2023，arXiv:2305.14627。

**任务：** 给定问题，让 LLM 生成带引用的长答案；评测回答的每个 statement 是否有引用支撑，以及每条引用是否必要。

**公式（按论文定义）：**

```text
citation recall    = (1/|S|) Σ_i 1[ C_i ≠ ∅ ∧ φ(concat(C_i), s_i)=1 ]
citation precision = (1/|C|) Σ_j 1[ c_j is relevant ]
```

其中 `S` 是回答拆出的 statement 集合，`C_i` 是 `s_i` 的引用集合，`φ` 是 NLI 蕴含判定（论文用 `t5_xxl_true_nli_mixture`）。一条引用 `c` 被判 **irrelevant** 需要同时满足：

1. `φ(c, s)=0`：单独看，`c` 不支撑 `s`；
2. `φ(concat(C_i \ {c}), s)=1`：去掉 `c` 之后，其余引用仍然支撑 `s`。

这个定义很关键：**它不要求“最小引用集”，冗余引用不扣分**。一条引用只要“单独不支撑、但去掉也不影响”，才算 irrelevant；如果去掉它之后其余引用不够，它仍是必要的（哪怕它单独不支撑）。这避免了“多引一条相邻条款就被罚”的误伤，但也意味着“堆引用”不会被 precision 惩罚——所以我们要另报 `pad_ratio` 和 `CP` 的“still-needed”版本。

**关键数字：**

- ELI5 上最好的模型仍有 **50%** 的生成缺乏完整引用支撑（论文摘要口径）。
- 人机一致性：citation recall 的 Cohen's κ = **0.698**；citation precision 的 κ = **0.525**。
- 以人工为金标准：recall 判定准确率 **85.1%**；precision 判定准确率 **77.6%**。
- 检测“insufficient citation”的 recall **82.3** / precision **84.2**；检测“irrelevant citation”的 recall **75.6** / precision **66.1**。
- §3.4 专门测了“top-1 段落自引/整段照抄”的作弊：模型倾向把整段复制当引用，recall 会虚高。

**一句话适用性：** citation recall 是我们 `CSR` 的直接先例，citation precision 的“去掉后是否仍支撑”是 `CP` 的正确口径；但 ALCE 的 `φ` 只判文本蕴含，**不判时效、管辖、效力**，所以它只能到 L2，不能到 L3。

**不能怎么用：**

- 不能把 ALCE precision 当“引用正确率”：一条 2019 年已废止的条款，只要文本上支持 statement，ALCE precision 会给 1。
- 不能只报 recall：模型可以给每个 statement 都挂一条“总则”引用，recall 很高，但具体条件没被支撑。
- 不能把 NLI 分数当人类判断：κ=0.525 的 precision 维度只是“中等一致”，必须人工抽检。

---

### 2.3 Attributed QA / AIS：归因是“According to P”，不是“事实为真”

**论文：** Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models，2022，arXiv:2212.08037。

**任务：** 模型回答问题时，同时给出支撑答案的原文片段；评测“答案是否由给定来源支撑”，而不是“答案是否符合世界”。

**AIS（Attributable to Identified Sources）定义：**

`(s,t)` 对来源 `P` 归因，当且仅当一个“泛听众”（generic hearer）在只读 `P` 的情况下，能有把握地说：“According to P, s.” 论文用 explicature 处理指代、省略、时态等上下文补充；只判 `P` 是否支撑 `s`，**不判 `P` 本身真假**。来源质量（source quality）是另一个维度，不能混进 AIS。

**AutoAIS：** 用 NLI 模型自动判 `P` 是否支撑 `s`；系统级 Pearson 相关：

- AIS vs EM = **0.45**；
- AIS vs AutoAIS = **0.96**。

实例级相关性低得多，论文明确提醒不要细读单条 AutoAIS 分数。→ **AutoAIS 可用于系统排序/开发期回归，不可用于单题验收，更不可让被评系统自己当 judge。**

**一句话适用性：** AIS 是“引用是否支撑”的黄金定义；中文政务落地时要把“泛听众”具体化为“不懂该业务的市民”，并把 `P` 限定为“现行有效、适用本管辖区的原文”。

---

### 2.4 AttrScore：把“支撑”拆成三类

**论文：** Automatic Evaluation of Attribution by Large Language Models（方法名 AttrScore，也常被称作 Attribution Score），2023/EMNLP Findings，arXiv:2305.06311。

**任务：** 输入 `(q, a, x)`（问题、答案、证据），输出三类：

- **attributable**：证据完全支持答案；
- **extrapolatory**：证据部分支持，但答案超出了证据（合理外推/补充）；
- **contradictory**：证据与答案矛盾。

**指标：** 三类分类准确率/宏 F1；论文还分析了错误类型。

**关键数字：**

- AttrEval-GenSearch 覆盖 **12 个领域**的 New Bing 测试集；
- GPT-4 zero-shot 总体 **81–83%**，显著优于其他模型，但论文认为仍不足以实用；
- 微调 FLAN-T5 770M 可超过 ChatGPT；Vicuna 13B zero-shot 34.6/41.4 → 微调后 66.0/71.3；
- 错误类型：fine-grained information insensitivity **30.6%**、overlooking contextual cues **79.4%**（多标签，合计 >100%）。

**一句话适用性：** 三类比二分类更贴近政务——很多回答不是“完全编造”，而是“引了文件，但把条件/范围外推了”；`extrapolatory` 应该单独统计，不能混进 `attributable`。

**代码：** `github.com/OSU-NLP-Group/AttrScore`。

---

### 2.5 VeriScore：claim 级事实性，不是引用级

**论文：** VeriScore: Evaluating the Factuality of Verifiable Claims in Long-Form Text Generation，2024，arXiv:2406.19276。

**任务：** 把长回答拆成原子 claim，逐条判断能否由检索证据支持；评“回答是否可验证”，不评“引了哪条”。

**公式：**

```text
support(c, E_c) ∈ {0,1}
S(r) = Σ_c support(c, E_c)          # 被支持的 claim 数
P(r) = S(r) / |C|                   # precision：回答里有多少 claim 被支持
R(r) = min(S(r) / K, 1)             # recall：相对该领域典型 claim 数 K 的覆盖
F1@K = 2PR / (P + R)                # S=0 时 F1=0
```

`K` 取该领域回答 claim 数的中位数；8 个长文本任务、16 个模型。→ **一句话适用性：** 适合评“回答整体事实性”，不适合替代 citation recall/precision；它不检查“这条 claim 引的是哪条”，也不检查时效/管辖。

---

### 2.6 生成式搜索审计：51.5% / 74.5%

**论文：** Evaluating Verifiability in Generative Search Engines，2023/EMNLP Findings，arXiv:2304.09848。

**任务：** 审计 Bing Chat、NeevaAI、perplexity.ai、YouChat 四个生成式搜索系统；人工判断每个生成句是否被其引用完全支撑、每条引用是否支撑其句。

**关键数字：**

- 平均仅 **51.5%** 的生成句被引用完全支撑；
- 仅 **74.5%** 的引用支撑其句；
- 也就是说：约一半句子“引了但没完全支撑”，约四分之一引用“挂了但没用”。

**一句话适用性：** 这是“引用存在 ≠ 引用支撑”的最直观证据；中文政务系统如果只报“引用覆盖率”，很可能落在同样的区间。

---

### 2.7 “引用真实但文件不适用”：misgrounded / stale citation

**定义。** 一条引用满足 L1（逐字存在）甚至 L2（文本上能推出），但满足不了 L3：

- **版本失效**：引的是 2019 年版本，2024 年已修订/废止；
- **法域错配**：引的是 A 省文件，回答 B 省问题；
- **主体错配**：引的是“企业”条款，回答“灵活就业人员”；
- **事项错配**：引的是“补贴标准”，回答“申请条件”；
- **效力错配**：引的是征求意见稿/内部讨论稿，不是生效文件；
- **层级错配**：引的是区级细则，但问题问的是市级/省级权限；
- **被推翻**：引的是被行政复议/司法判决推翻的旧口径。

这类引用在“文本蕴含”指标上通常是满分，但在法律/政策意义上就是幻觉。

**Magesh et al. 2024（arXiv:2405.20362）** 是目前最直接的实证：

- 三个商用法律 RAG 在 **17%–33%** 的时间产生幻觉；
- Lexis+ AI 准确率 **65%**；Westlaw AI-AR **42%**；Ask Practical Law AI **60%+** 查询 incomplete（拒答或 ungrounded）；
- 明确区分 grounded / ungrounded / **misgrounded** / hallucinated；
- 关键警告：CS 的 “grounded” 只看“是否忠于给定文档”，**misgrounded 在 CS 指标里可能拿满分**。

**Legal warrant 2026（arXiv:2609.17546，AI4Law@ICML 2026）** 是目前最接近“适用性”的专门框架，但只是 position paper：

- claim-authority warrant 五条件：权威存在、适用于相关管辖、在分析时点有效、法律效力状态被正确表示、支持该命题；
- 还要求混合响应策略：回答/限缩/追问/警告/纠正错误前提/弃权；
- pilot 很小，指标未标准化。

**CitaLaw 2024（arXiv:2412.14556）** 是中文法律引用的直接先例：

- 论文称首个中文法律引用 benchmark；Layperson 500 题 + Practitioner 500 题，共 **1,000 题**；语料约 **500,000** 文档（法条+判例）；
- 2 个开放域 + 7 个法律 LLM；
- 三段论三维：大前提（法条 NLI 蕴含句子）、小前提（事实/违法行为）、结论（法律决定），并测与用户问题的一致性；
- 与人工判断的 Cohen's κ = **0.7876**（Qwen2 标注 vs 人）；DISC-LawLLM 作 NLI。

**结论：** “引用真实但文件不适用”**没有专门的标量指标**。最接近的是 Magesh 的 `misgrounded` 分类、Legal warrant 的五条件、CitaLaw 的三段论；但它们分别是“错误分类”“研究议程”“法律领域 benchmark”，不是中文政务 RAG 的通用指标。我们要自己定义 `AC` / `misgrounded_rate` / `stale_rate`。

**可落地的三个指标：**

```text
AC               = #applicable citations / #citations
misgrounded_rate = #(locatable=1 ∧ sufficient=1 ∧ applicable=0) / #citations
stale_rate       = #(source.repealed_at ≤ as_of ∨ version ≠ current_version) / #citations
```

`applicable` 的判定优先级：

1. **元数据硬规则**：`doc.effective_from ≤ as_of < doc.repealed_at`；`doc.jurisdiction` 与 `query.jurisdiction` 匹配；`doc.doc_type` 不是“征求意见稿/内部稿”；`doc.authority_level` 与问题权限匹配。
2. **条款级规则**：`span` 的主体/事项与 `query` 匹配（企业/个人、补贴/处罚、申请/发放）。
3. **人工抽检**：对“新旧法衔接”“特别法优于一般法”“上位法/下位法冲突”等灰区，人工判；每层至少 10% 双人标注，报 κ。

---

### 2.8 中文政务/法律的引用评测实践：有先例，没有现成 benchmark

- **CitaLaw（法律）**：唯一直接的中文引用 benchmark；但它是法律领域（法条+判例），不是政务政策（通知/办法/细则/补贴标准），也不覆盖“城市/区县”管辖。
- **POLIS-Bench（政务）**：有 evidence span，但指标是 semantic similarity + LLM-judge accuracy，**不评引用归属**；它是“政策任务评测”，不是“引用可验证性评测”。
- **MSGABench（政务）**：中文政务 LLM benchmark；15 个 LLM；部分模型轻微输入变化下 <35% accuracy；GPT-4 >95% reliability；发现过度谨慎/任务回避；**不评 RAG 引用**。
- **PolicyBench（政策）**：21K cases、US-China 跨体系；Bloom 三层 Memorization/Understanding/Application；**闭卷政策理解**，不是 RAG。
- **ALCE/AIS/AttrScore/VeriScore**：语言无关，但 NLI/judge 需要中文法律/政务领域适配；阈值和 κ 必须中文重标，不能直接搬英文数字。

**中文政务引用的最低要求（建议写进规范）：**

每条规范性命中必须带：

```json
{
  "doc_id": "京人社发〔2024〕X号",
  "doc_title": "...",
  "article": "第N条",
  "span": "逐字引文",
  "url": "https://...",
  "publisher": "北京市人力资源和社会保障局",
  "publish_date": "2024-...",
  "effective_from": "2024-...",
  "repealed_at": null,
  "jurisdiction": "北京市",
  "doc_type": "规范性文件",
  "version": "2024修订"
}
```

没有 `effective_from`、`jurisdiction`、`doc_type` 的引用，只能算 L1，不能算 L3；在政务场景不应作为“已核实”展示。

---

### 2.9 引用指标总表：测什么，不测什么

| 指标 | 公式 | 测什么 | 不测什么 |
|---|---|---|---|
| `CLR` | `#locatable/#citations` | 引文是否真实存在、可回查 | 是否支撑、是否适用 |
| `CSR` | `#claims with ≥1 sufficient citation/#claims` | 每个 claim 是否有支撑 | 引用是否冗余、是否适用 |
| `AC` | `#applicable/#citations` | 版本/法域/主体/事项是否匹配 | 文本是否真的支撑 |
| `CP` | `#citations(relevant ∧ still-needed)/#citations` | 引用是否必要、精确 | 是否覆盖所有 claim |
| `pad_ratio` | `citation_len/minimal_support_len` | 是否整段照抄/堆引用 | 内容是否正确 |
| ALCE recall | 见 §2.2 | statement 级引用覆盖 | 时效/管辖 |
| ALCE precision | 见 §2.2 | 引用是否可去掉 | 冗余堆引用 |
| AIS | 人工/ AutoAIS | 来源是否支撑答案 | 来源本身真假 |
| AttrScore | 3 类分类 | 完全支持/外推/矛盾 | 时效/管辖 |
| VeriScore | `F1@K` | 回答 claim 的事实性 | 引了哪条 |
| RAGAS Faithfulness | `\vert V\vert/\vert S\vert` | 回答是否忠于 context | 引用归属（会漏“引 A 结论来自 B”） |
| `misgrounded_rate` | L1∧L2∧¬L3 | 引用真实但不适用 | 文本不支撑（那是 L2） |
| `stale_rate` | 失效/旧版占比 | 版本时效 | 是否支撑、是否适用主体 |

**一句话选择建议：**

- 只做工程验收：先看 `CLR`；低于 0.98 不要谈语义。
- 做生成质量：看 `CSR` + `CP` + `unsupported_claim_rate`。
- 做政务合规：必须加 `AC` + `misgrounded_rate` + `stale_rate`。
- 做对外声明：四个全报，并报人工 κ 和样本量；只报一个“引用准确率”没有意义。

## 3. 中文 RAG 评测

### 3.1 总表：有什么，缺什么

| 工作 | 年份 | 领域/任务 | 核心指标（公式级） | 关键数字 | 对中文政务的适用性 |
|---|---|---|---|---|---|
| CRUD-RAG | 2024 | 中文新闻 RAG；Create/Read/Update/Delete 四任务 | 检索 `Recall@k`、`Precision@k`；生成 `ROUGE-L`、`BLEU`、`BERTScore`、`RAGQuestEval` | 30 万篇→>8 万篇；4 任务 | 可借任务分解；新闻非政务，不评引用/拒答 |
| DomainRAG | 2024 | 中文高校招生；6 项能力 | `EM`（gold 被预测包含）、`EMS`（严格相等）、`F1`、`ROUGE-L`、`GE`（GPT-4 judge） | 6 能力/共享语料 | 最接近中文领域 RAG 评测设计；非政务 |
| RGB | 2024 | 中英 RAG；noise/negative rejection/integration/counterfactual | `accuracy`、`rejection rate`、`noise robustness` | 4 能力/6 LLM；negative rejection 弱 | 中文 RAG 拒答参考；不评引用 |
| T2Ranking | 2023 | 中文 passage ranking | `nDCG@k=DCG@k/IDCG@k`、`MRR@k`、`Recall@k`；4 级相关 | >300K queries/>2M passages | 可借分级相关/假阴性缓解；非政务 |
| C-MTEB | 2023 | 中文 embedding 评测；6 类任务/35 数据集 | 检索 `nDCG@10`；分类/聚类/STS 各自准确率/相关系数；总分为任务平均 | 6/35；比先前 SOTA 最高 +10% | 用来选 embedding；不是端到端 RAG |
| DuRetrieval | C-MTEB 子集 | 中文检索 | `nDCG@10`；HF 卡 corpus 100,001/queries 2,000（待核） | 规模与坊间说法冲突 | 可作检索回归；非政务 |
| MedBench | 2023 | 中文医疗；4 部分 | `accuracy`（按考试/病例分节） | 40,041 题；4 部分 | 医疗知识与推理；不是 RAG 引用/拒答 |
| Huatuo-26M | 2023 | 中文医疗 QA 语料 | 检索 `Recall@k`；生成 `BLEU/ROUGE` 等 baseline | 26M QA | 可作外部知识库；不是评测 benchmark |
| POLIS-Bench | 2025 | 双语政务政策；3 任务 | `semantic similarity` + `LLM-judge accuracy`；具体权重待核 | 3,000 题（1,000×3）；>10 LLM | 有政务评测；不评引用归属/拒答 |
| PolicyBench | 2026 | 美中政策理解；3 层 | `accuracy` by Memorization/Understanding/Application | 21K cases | 闭卷政策理解；不是 RAG |
| MSGABench | 2025 | 中文政务 LLM | `accuracy`/`reliability` | 15 LLM；部分 <35%；GPT-4 >95% | 有政务 benchmark；不评 RAG 引用 |

**一句话结论：** 中文 RAG 有 CRUD-RAG/DomainRAG/RGB 这类“端到端任务”评测，有 T2Ranking/C-MTEB/DuRetrieval 这类“检索/embedding”评测，也有 POLIS/MSGABench/PolicyBench 这类“政务/政策”评测；但**没有公开的“中文政务 RAG 引用可验证性 + 拒答”benchmark**。必须自己建。

---

### 3.2 CRUD-RAG：把 RAG 拆成四个动作

**论文：** CRUD-RAG: A Comprehensive Chinese Benchmark for Retrieval-Augmented Generation of Large Language Models，2024/TOIS，arXiv:2401.17043。

**任务：** 中文新闻语料；按数据库 CRUD 拆四类：

- **Create**：文本续写（给定开头，生成后续）；
- **Read**：单文档/多文档 QA；
- **Update**：幻觉修改（给定错误文本，要求按证据修正）；
- **Delete**：多文档摘要（删繁就简）。

**语料：** 爬取 2023-07 之后的 300,000 篇中文新闻，去重/过滤后 **>80,000 篇**。

**指标：**

```text
Retrieval Recall@k    = #queries with ≥1 relevant doc in top-k / #queries
Retrieval Precision@k = #relevant docs in top-k / k
ROUGE-L               = LCS-based F-measure
BLEU                  = n-gram precision with brevity penalty
BERTScore             = cosine similarity of contextual embeddings
RAGQuestEval          = 用 QA 对反查生成文本是否覆盖参考答案要点
```

**关键发现（论文分析维度）：** chunk size、chunk overlap、embedding 模型、retriever、LLM 都会影响四类任务，**没有一套配置在四类任务上同时最优**；检索好不等于生成好，生成好也可能检索差。

**一句话适用性：** 四任务分解可以直接借来设计政务评测（政策续写/政策问答/错误口径修正/多文件摘要）；但它是新闻语料，不评引用、不评拒答、不评时效。

---

### 3.3 DomainRAG：六项能力，最像“领域 RAG 评测设计”

**论文：** DomainRAG: A Chinese Benchmark for Evaluating Domain-specific Retrieval-Augmented Generation，2024，arXiv:2406.05654。

**领域：** 中文高校招生（政策/专业/分数线/志愿等），共享一个领域语料库。

**六项能力：**

1. **Conversational RAG**：多轮对话中的检索与指代消解；
2. **Structural information analysis**：表格/结构化管理信息（招生计划、分数线）的理解；
3. **Faithfulness to external knowledge**：回答是否忠于检索到的外部知识；
4. **Denoising**：检索结果混入噪声时的鲁棒性；
5. **Time-sensitive problems**：分年度/批次的政策时效；
6. **Multi-document interactions**：跨文档整合。

**指标：** 每项能力一个数据集；论文 4.2 用四种指标，不压成一个统一总分：

```text
EM   = 1[gold answer is contained in prediction]
EMS  = 1[prediction == gold answer]          # 严格相等
F1   = token-level F1                         # term matching
ROUGE-L = longest-common-subsequence F-measure
GE   = GPT-4 judge score                      # 长答案（conversational / multi-doc）
```

- `EM/EMS/F1/ROUGE-L` 用于短答案/结构化题；`GE` 用于多轮与跨文档长答案，让 GPT-4 判“预测是否与参考答案一致”（具体分制/提示词**待核**）。
- 关键：**不造“RAG 总分”**，按能力分开报；这比一个综合分更能定位失败。

→ 这正是我们该学的：**不要造一个“RAG 总分”**，按能力分开报。

**关键发现：** 闭卷 LLM 在招生领域表现吃力；检索能帮忙，但不是所有能力都线性提升；结构化信息和时效题是明显短板。

**一句话适用性：** 六项能力几乎可以一一映射到政务 RAG（多轮、表格、忠实、噪声、时效、跨文件）；但它是高校招生，不是政务，也没有引用/拒答指标。

---

### 3.4 RGB：中文 RAG 的 negative rejection

**论文：** Benchmarking Large Language Models in Retrieval-Augmented Generation，2023/AAAI 2024，arXiv:2309.01431。

**任务：** 中英双语 RAG；四项能力：

- **Noise robustness**：检索结果混入无关文档；
- **Negative rejection**：检索结果没有答案时，能否拒答；
- **Information integration**：多文档信息整合；
- **Counterfactual robustness**：检索到与参数知识冲突的内容时，能否以检索为准。

**指标：**

```text
accuracy         = #correct / #answerable
rejection_rate   = #refused / #unanswerable
noise_robustness = accuracy under noisy retrieved set
```

6 个 LLM。**关键数字/结论：** negative rejection 是显著弱项；模型在“检索不到答案”时倾向硬答。→ **一句话适用性：** 中文 RAG 拒答的合适参考；但它的 rejection 是“当前检索结果没有答案”，不区分“语料没有”与“世界没有”，也没有引用指标。

---

### 3.5 中文检索评测：T2Ranking / C-MTEB / DuRetrieval

#### T2Ranking

**论文：** T2Ranking: A Large-scale Chinese Benchmark for Passage Ranking，2023/SIGIR，arXiv:2304.03679。

**任务：** 中文 passage ranking；>300K queries、>2M unique passages；4 级相关度（0/1/2/3）。

**指标：**

```text
nDCG@k = DCG@k / IDCG@k
DCG@k  = Σ_{i=1..k} (2^{rel_i}-1) / log2(i+1)
MRR@k  = (1/|Q|) Σ_q 1[rank_q ≤ k] / rank_q
Recall@k = #queries with ≥1 relevant passage in top-k / #queries
```

**关键做法：** 论文显式用更多样化的段落来缓解 **false negative**（未标注≠不相关），尤其 test set；这是中文检索评测里少见的“承认 qrels 不完整”的工作。→ **一句话适用性：** 可借 4 级相关和假阴性缓解；但它是通用 passage ranking，不是政务 qrels。

#### C-MTEB / C-Pack

**论文：** C-Pack: Packed Resources for General Chinese Embeddings，2023/SIGIR 2024，arXiv:2309.07597。

**任务：** 中文 embedding 评测；C-MTEB 含 **6 类任务/35 个数据集**（检索、分类、聚类、STS、pair classification、reranking）；C-MTP 训练数据；C-TEM 模型。

**指标：** 检索用 `nDCG@10`；分类/聚类用 accuracy/NMI；STS 用 Spearman；总分为任务平均。关键数字：中文 embedding 比先前 SOTA 最高 **+10%**。

**一句话适用性：** 用来选 embedding/检索模型，不是端到端 RAG；政务领域需要在自己的 qrels 上重测，不能直接看榜首。

#### DuRetrieval

**论文/资源：** C-MTEB 的 DuRetrieval 子集；HF 数据集卡 `C-MTEB/DuRetrieval`。

**任务：** 中文段落检索；C-MTEB 检索任务之一，指标 `nDCG@10`。

**规模：** HF 卡显示 corpus **100,001** 条、queries **2,000** 条（`num_examples`）；这与坊间“100k queries / 12M passages”的说法冲突。**正文只按 HF 卡引用，规模标“待核”**，不要写死 12M。

**一句话适用性：** 可作中文检索回归集；规模/领域都不匹配政务，不能当政务覆盖度证据。

---

### 3.6 中文医疗 RAG：有 benchmark，但没有引用评测

#### MedBench

**论文：** MedBench: A Large-Scale Chinese Benchmark for Evaluating Medical Large Language Models，2023/AAAI-24，arXiv:2312.12806。

**任务：** 中文医疗；**40,041 题**，四部分：

1. 执业医师考试；
2. 住院医师规范化培训考试；
3. 主治医师资格考试；
4. 真实临床病例。

**指标：**

```text
accuracy = #correct answers / #questions          # 按四部分/科目分节
```

评知识/推理，不是 RAG 引用/拒答。→ **一句话适用性：** 可作为医疗领域能力基线；不能直接迁移到政务，也不测“引用是否可验证”。

#### Huatuo-26M

**论文：** Huatuo-26M, a Large-scale Chinese Medical QA Dataset，2023，arXiv:2305.01526。

**任务：** **26M** 中文医疗 QA 对；可作 RAG 外部知识库；论文给检索/生成 baseline。

**指标：**

```text
retrieval Recall@k = #queries with ≥1 relevant QA doc in top-k / #queries
generation BLEU/ROUGE = n-gram overlap against reference answers
```

不是引用评测。→ **一句话适用性：** 语料规模参考；医疗 QA 的“正确答案”不等于“可引用证据”，不能拿它当 citation benchmark。

**中文医疗 RAG 评测的空白：** MedBench 评知识，Huatuo 给语料；两者都不评“引用是否定位/支撑/适用”，也不评“没检索到 vs 医学上无定论”。如果要做医疗 RAG，需要临床医生参与标注 L3（指南版本/适用人群），不能只用考试 accuracy。

---

### 3.7 政务/政策问答：有公开评测，但没有 RAG 引用/拒答评测

#### POLIS-Bench

**论文：** POLIS-Bench: Towards Multi-Dimensional Evaluation of LLMs for Bilingual Policy Tasks in Governmental Scenarios，2025，arXiv:2511.04705。

**任务：** 双语政务政策；三个任务：

1. **Clause Retrieval & Interpretation**：条款检索与解释；
2. **Solution Generation**：按政策给方案；
3. **Compliance Judgment**：合规判断。

**规模/指标：** 每任务 1,000 题，共 **3,000**；>10 个 LLM；dual metric = semantic similarity + LLM-judge accuracy；有 evidence span；具体权重/阈值**待核**。形式化：

```text
semantic_similarity = cos(emb(answer), emb(reference))
accuracy            = #LLM-judge correct / #questions
```

**一句话适用性：** 目前最接近“中文政务政策 RAG 任务”的公开 benchmark；但它评“任务答案对不对”，不评“引用是否可验证”“该不该拒答”。**不能把它当 citation/abstention benchmark。**

#### PolicyBench / PolicyLLM

**论文：** PolicyLLM: Towards Excellent Comprehension of Public Policy for Large Language Models（benchmark 名 PolicyBench），2026/ACL 2026 Findings，arXiv:2604.12995。

**任务：** 21K cases，美中跨体系政策理解；Bloom 三层：Memorization / Understanding / Application；PolicyMoE 模型。

**指标：**

```text
accuracy_layer = #correct at layer / #questions at layer
                  # layer ∈ {Memorization, Understanding, Application}
```

→ **一句话适用性：** 闭卷政策理解，不是 RAG；可借“三层认知难度”设计题目，但不能用来证明检索/引用能力。

#### MSGABench

**论文：** The Evaluation Framework and Benchmark for Large Language Models in the Government Affairs Domain，2025，ACM TIST，DOI `10.1145/3716854`（Shuo Liu 等）。

**任务：** 中文政务 LLM 评测框架 + MSGABench 数据集；15 个 LLM。

**指标：**

```text
accuracy   = #correct / #questions
reliability = pass rate under minor input perturbations
```

关键数字：部分模型在轻微输入变化下 **<35%** accuracy；GPT-4 reliability **>95%**；论文发现过度谨慎/任务回避。→ **一句话适用性：** 证明“中文政务 benchmark 存在”，但它不是 RAG 引用可验证性评测；“过度谨慎”也不等于“正确拒答”。

#### CitaLaw（法律，不是政务）

已在 §2.7 详述：1,000 题、约 500k 文档、三段论三维、κ=0.7876。它是**中文法律引用**的直接先例；政务政策（通知/办法/细则/补贴标准）在文件类型、时效、管辖层级上更碎，不能直接套。

**结论：**

- **有**公开中文政务评测：POLIS-Bench、MSGABench、PolicyBench；
- **有**公开中文法律引用评测：CitaLaw；
- **没有**公开的“中文政务 RAG 引用可验证性 + 拒答”benchmark；
- **没有**公开的“中文政务检索 qrels”（T2Ranking/DuRetrieval 都不是政务）。

这不是“文献没做好”，而是政务数据的管辖碎片化、时效性强、标注需要业务专家，公开成本高。我们的 313/337 城市语料反而是一个稀缺资产；应该把它做成可复现的内部 benchmark。

---

### 3.8 可以借什么，不能借什么

**可以借：**

- **CRUD-RAG 的四任务**：Create/Read/Update/Delete → 政务可映射为“政策续写/政策问答/错误口径修正/多文件摘要”。
- **DomainRAG 的六能力**：多轮、结构化、忠实、噪声、时效、跨文档 → 直接对应政务 RAG 的失败模式。
- **RGB 的 negative rejection**：检索结果没有答案时该不该拒答 → 对应我们的 `NOT_COVERED` 子集。
- **T2Ranking 的 4 级相关 + 假阴性缓解**：政务 qrels 也会有“未标注≠不相关”，要用 graded relevance 和多路召回补标。
- **C-MTEB 的检索 nDCG@10**：选 embedding 的快速回归；但最终要自己的 qrels。
- **CitaLaw 的三段论**：大前提（法条）→ 小前提（事实）→ 结论；政务可改为“政策依据→适用条件→办理结论”。

**不能借：**

- 不能把 CRUD-RAG 的 ROUGE/BLEU 当政务回答质量：政策回答的关键是条件/材料/时限是否齐全，不是 n-gram 重叠。
- 不能把 MedBench 的 accuracy 当医疗 RAG 质量：考试题有标准答案，真实咨询没有。
- 不能把 POLIS 的 semantic similarity 当引用可验证性：语义相似不代表引对了条款。
- 不能把 MSGABench 的“过度谨慎”当“正确拒答”：过度谨慎可能是该答不答，正确拒答是该不答不答。
- 不能把 T2Ranking/C-MTEB 的榜首当政务检索效果：领域/粒度/时效都不同。

---

### 3.9 如果要做“中文政务 RAG 评测集”，最小设计

| 维度 | 建议 |
|---|---|
| 城市 | 至少 6 个：4 直辖市 + 2 个地级市；覆盖“已接入/未接入” |
| 政策类型 | 补贴、社保、公积金、落户、企业扶持、处罚/许可 |
| 题目类型 | 事实查询、条件判断、材料清单、时限、跨文件、时效（新旧政策）、无规定、未接入、歧义 |
| 每格样本 | ANSWERABLE 200；NOT_COVERED（世界有）100；NOT_COVERED（世界无）50；NO_POLICY 100；OUT_OF_SCOPE 50；CLARIFY 50 |
| 引用标注 | 每条规范 claim 标 `doc_id/article/span/effective_from/jurisdiction/doc_type`；双人标注，报 κ |
| 检索标注 | 每题标 `gold_doc_ids`；至少两路召回 + 人工补标，报 `unjudged_rate` |
| 语料快照 | 固定 `corpus_snapshot` + `index_version`；每次换快照重跑 coverage |
| 发布门禁 | 四条硬断言（§1.3.4）+ `CLR≥0.98` + `AC≥0.95` + `CSR≥0.90`（阈值可按业务调，但要先定） |

这个设计不追求“榜单排名”，追求“能定位失败在哪一层”：语料没接入、检索没召回、阅读器没用、引用不适用、该拒答没拒。**这比一个总分更有用。**

## 4. 语料/覆盖度

### 4.1 结论先行：几乎没有被当成一阶指标

“语料完整性/覆盖度”在 RAG 论文里通常不是一个独立指标，而是被三个东西替代：

1. **检索指标**：`Recall@k` / `nDCG@k`，但需要 qrels；qrels 只覆盖“已标注的池子”，不是“世界上所有相关文档”。
2. **答案指标**：accuracy / faithfulness，但失败可能来自语料缺失、检索没召回、阅读器不会用，三者混在一起。
3. **语料规模**：文档数、token 数、城市数；这是“投入”，不是“覆盖”。

**必须先区分五个“coverage”：**

| 名称 | 分母 | 回答的问题 | 常见误用 |
|---|---|---|---|
| `source_coverage` | 目标语料全集 `U`（城市×政策类型×年份） | 我们接入了多少“应该接入”的来源 | 用“文档数”代替；用“城市数”代替“政策类型覆盖” |
| `corpus_recall` | 现实世界有答案的问题集 `Q_world`（`world_has=true`，含 `ANSWERABLE` 与 `NOT_COVERED`） | 世界有答案的问题里，gold 证据在语料中的比例 | 用 retrieval recall 代替；后者还要求“被检索到” |
| `retrieval_recall@k` | gold 已入语料的问题 `Q_index`（`index_has=true`） | gold doc 是否进入 top-k | 把“语料没有”算成“检索失败” |
| `answer_coverage` | 全部问题 `Q` | `#correct/#all`；系统答对了多少 | 和 selective prediction 的 coverage 混淆 |
| `answered_rate` | 全部问题 `Q` | `#answered/#all`；系统敢答多少 | 把“敢答”当“答对” |

选择性预测里的 `coverage` 是“回答了多少”，不是“语料覆盖了多少”。两个词都叫 coverage，但分母完全不同；报告里必须写全称，不要只写“覆盖率”。

---

### 4.2 为什么“语料完整性”难测

- **没有全世界分母。** “所有政务政策”不是一个有限、可枚举的集合：国家/省/市/区县/街道，法律/法规/规章/规范性文件/通知/办事指南，现行/失效/征求意见。你必须先定义一个 `U`，否则“覆盖率”没有意义。
- **文件在变。** 同一政策 2019/2021/2024 三个版本，引用错版本就是 misgrounded；语料快照必须带 `as_of` 和 `version`。
- **相关性判断不完整。** TREC 式 pooling 只判“池子里出现的文档”；未出现的相关文档被当成不相关，这就是 pooling bias（Zobel 1998）。bpref（Buckley & Voorhees 2004）是对不完整判断的缓解，不是彻底解决。
- **“没找到”与“不存在”无法从语料内部区分。** 语料里没有，可能是没接入、没索引、没召回，也可能是世界上确实没有规定；只有外部金标（`world_has`）能区分。

**两个经典方法：**

- **Zobel 1998**，*How reliable are the results of large-scale information retrieval experiments?*，SIGIR 1998，DOI `10.1145/290941.291014`：系统性地指出 pooling 只覆盖部分系统的结果，未池化的相关文档会低估 recall；不同 pooling 深度会改变系统排名。→ **适用性：** 政务 qrels 如果只用一个检索器 pool，就会把“这个检索器找不到但别的检索器能找到”的文档判为不相关；必须多路召回 + 人工补标。
- **Buckley & Voorhees 2004**，*Retrieval evaluation with incomplete information*，SIGIR 2004，DOI `10.1145/1008992.1009000`：提出 bpref，用“已判不相关文档排在相关文档之前的数量”来算，不要求所有文档都被判；公式：

```text
bpref = (1/R) Σ_{r∈R} ( 1 - |n ranked above r| / min(R, N) )
```

其中 `R` 是已判相关文档数，`N` 是已判不相关文档数。→ **适用性：** 当我们只有部分 qrels 时，bpref 比 recall 稳；但它仍依赖“已判集合”，不能证明语料完整。

---

### 4.3 现有指标能做到哪一步

| 指标/工作 | 公式/口径 | 能回答 | 不能回答 |
|---|---|---|---|
| `Recall@k` | `#queries with ≥1 gold doc in top-k / #queries` | 检索器在已标注池子上的召回 | 语料是否完整；未标注的相关文档 |
| `nDCG@k` | `DCG@k/IDCG@k` | 排序质量 | 语料完整性 |
| RAGAS `context_recall` | `#reference claims supported by retrieved context / #reference claims` | 检索上下文覆盖参考答案要点的比例 | 分母是“参考答案 claim”，不是“语料”；不能当语料覆盖 |
| CRAG `missing rate` | `#missing/#all` | 答案级“没答出来”的比例 | 没答出来的原因；语料/检索/模型混在一起 |
| UAEval `Out-of-Database` | 按类 `Acceptable Ratio` | 模型在“库外”题上的表现 | 库外但世界有 vs 库外且世界无；没有 2×2 |
| DomainRAG `denoising/time-sensitive` | 各能力数据集准确率 | 噪声/时效能力 | 语料覆盖度；没有“未接入”金标 |
| RAGChecker / RAGBench | claim-level entailment / TRACe | 检索与生成的细粒度诊断 | 语料完整性；它们以给定 context 为边界 |
| `source_coverage`（自建） | `\vert ingested sources\vert/\vert U\vert` | 应该接入的来源接入了多少 | 接入的来源是否真的能答；需要配合 `corpus_recall` |

**关键结论：** 没有任何一个现成指标是“语料完整性”的一阶测量。最接近的是 retrieval recall + qrels，但它的分母是“已标注问题”，不是“世界上的所有政策”。RAGAS context recall 更不是：它的分母是参考答案的 claim，语料没有参考文档时，它甚至无法构造。

---

### 4.4 自建指标：把“覆盖”拆成可计算的分母

#### （1）`source_coverage`：来源级覆盖

先定义目标全集：

```text
U = { (city, policy_type, year) }        # 例：337 个城市 × 6 类政策 × 现行版本
source_coverage = |ingested ∩ U| / |U|
```

报告要求：

- 分子/分母都要写清楚：是“城市数”还是“城市×政策类型”的格子数；
- 按 `policy_type`、`region`、`year` 分层；
- 每个比例带 Wilson 95% CI；
- 零缺失的格子报单侧 Clopper-Pearson 上界，不写“100% 覆盖”。

**用我们自己的例子：** 入口横幅写“已接入 313/337 个城市（含 4 个直辖市；语料版本 cv-20261111-1），24 个未接入”。

- 这是 **城市级** `source_coverage = 313/337 = 92.9%`；按 337 个城市算，Wilson 95% CI 约 **[89.6%, 95.2%]**（按公式计算，非论文数字）。
- 它**不是**政策级覆盖：一个城市“已接入”可能只覆盖社保，不覆盖公积金/落户/补贴；如果按“城市×政策类型”算，分母可能是 337×6=2,022 个格子，覆盖率会明显低于 92.9%。
- 因此横幅必须写“已接入 313/337 个城市”，不能写“语料覆盖率 92.9%”；后者会被误解为“所有政策都覆盖了 92.9%”。

#### （2）`corpus_recall`：问题级语料召回

```text
corpus_recall = #Q with gold evidence in corpus / #Q_world
              # Q_world = {q : gold.world_has=true} = ANSWERABLE ∪ NOT_COVERED
```

- 分母是“现实世界有答案的问题”，**不是“所有问题”**；`NO_POLICY`（`world_has=false`）不应该拉低语料召回，也不应该算进分子。
- `NO_POLICY` 单独报 `no_policy_evidence_recall`：能否在语料中定位到“规定空白”所依据的文件；它衡量的是“无规定”结论的证据基础，不是答案召回。
- 需要 `gold_doc_ids` 和 `corpus_snapshot`；同一问题换快照要重算。
- 按 `ANSWERABLE` / `NOT_COVERED`（`world_has=true`）分层报；`NOT_COVERED` 的分子为 0，正好量化“世界有、我们没接入”。
- 如果 `corpus_recall` 低，但 `retrieval_recall` 高，说明“语料有、检索没召回”；如果两者都低，说明“语料没接入”。

#### （3）`retrieval_recall@k` 与 `unjudged_rate`

```text
retrieval_recall@k = #queries with ≥1 gold doc in top-k / #queries_with_gold_in_corpus
unjudged_rate      = #retrieved docs not in qrels / #retrieved docs
```

- `retrieval_recall@k` 的分母只含 `gold.index_has=true` 的问题；`NOT_COVERED`（库无、世界有）先由 `corpus_recall` 记为语料缺失，不能算成检索失败。
- `unjudged_rate` 高，说明 qrels 不完整，recall 不可信；先补标，再报 recall。
- 多路召回（BM25/embedding/LLM query rewrite）+ 人工抽检，降低 pooling bias。
- 报 `bpref` 作为不完整判断下的稳健指标。

#### （4）`no_source_rate` / `retrieval_miss_rate` / `reader_miss_rate`

```text
no_source_rate      = #failures with gold doc absent from corpus / #failures
retrieval_miss_rate = #(gold.index_has=true ∧ gold_doc ∉ top_k) / #(gold.index_has=true)
reader_miss_rate    = #(gold_doc ∈ top_k ∧ pred≠answer) / #(gold_doc ∈ top_k)
```

这三个分母把“语料没接入”“检索没召回”“阅读器不会用”分开；没有它们，accuracy 的失败原因不可解释。

#### （5）`coverage_delta`：接入后的边际收益

```text
coverage_delta = source_coverage(t+1) - source_coverage(t)
answer_delta   = answer_rate(t+1) - answer_rate(t)   # 同一题集、同一模型
```

只报“又接入了 N 个城市”没有意义；要报“同一题集上，answer_rate 提升了多少”。如果 `coverage_delta` 为正但 `answer_delta` 为 0，说明新接入的语料没有被检索/阅读器用起来，或者题目没有覆盖新城市。

---

### 4.5 “检索池缺失”导致的假阴性

这是语料评测里最容易被忽略的偏差：**gold 文档根本不在语料里，检索器不可能召回；评测却把失败算在模型头上。**

三种缺失，修复动作不同：

| 缺失类型 | 定义 | 指标 | 修复 |
|---|---|---|---|
| `source_missing` | 来源从未接入（如 24 个未接入城市） | `source_missing_rate` | 接语料 |
| `index_missing` | 来源已接入，但切块/过滤/索引丢了 | `index_missing_rate` | 重建索引/修解析 |
| `retrieval_miss` | 已索引，但 top-k 没召回 | `retrieval_miss_rate` | 修检索/query rewrite |
| `reader_miss` | 召回了 gold doc，模型没用 | `reader_miss_rate` | 修 prompt/阅读器 |

**评测偏差的例子：**

假设 337 个城市中 24 个未接入（7.1%）。如果测试题按城市均匀采样，预期 7.1% 的题会因 `source_missing` 而失败；如果评测集故意多采未接入城市（为了测“承认没接入”），失败率会更高。**如果不报测试集的城市构成，读者会把“语料缺失”误读成“模型能力差”。**

正确做法：

- 测试集按 `ANSWERABLE / NOT_COVERED / NO_POLICY / OUT_OF_SCOPE` 分层，报每层的 n 和来源城市；
- 对 `NOT_COVERED` 再标 `source_missing` vs `retrieval_miss`；
- 报 `no_source_rate` 时，分母是“所有失败”，不是“所有题”；并给出 failure taxonomy 的完整分布；
- 如果要做“模型能力”比较，只在 `ANSWERABLE` 且 `gold_doc ∈ corpus` 的子集上比；否则语料差异会污染模型排名。

---

### 4.6 数据污染：另一种“假阴性/假阳性”

语料覆盖度低会带来假阴性（该答的答不了）；数据污染会带来假阳性（没检索也能答对，或评测题在预训练里）。

- **污染综述 2024/TMLR 2025**（[arXiv:2404.00699](https://arxiv.org/abs/2404.00699)）：系统梳理训练数据与评测集重叠的检测方法（n-gram、embedding、表现异常、成员推断等）。→ **适用性：** 用来设计“政务题是否在预训练里”的检查；不能证明没有污染，只能降低风险。
- **LiveBench 2024/ICLR 2025 Spotlight**（[arXiv:2406.19314](https://arxiv.org/abs/2406.19314)）：频繁更新题目、客观自动评分、contamination-limited。→ **适用性：** 借“滚动更新 + 截止日期”的思路；政务可以用“模型 cutoff 之后发布的新政策”造题。
- **PoisonedRAG 2024/USENIX Security 2025**（[arXiv:2402.07867](https://arxiv.org/abs/2402.07867)）：向知识库注入少量恶意文本即可诱导目标答案；RAG 知识库是新攻击面。→ **适用性：** 语料覆盖度之外，还要测“语料被污染/被对抗”时的引用可靠性；这不是 completeness，但同属“语料质量”。

**可落地的两个指标：**

```text
closed_book_accuracy = accuracy without retrieval
contamination_gap    = closed_book_accuracy - accuracy_with_retrieval
citation_only_rate  = #correct answers with valid citation / #correct answers
```

- `contamination_gap` 大：模型可能靠参数知识答题，RAG 的增益被高估；也可能是检索没帮上忙。要结合 `citation_only_rate` 看。
- `citation_only_rate` 低：答案对但引用不成立，说明“答对了但不是 RAG 的功劳”，不能算引用可验证。
- 对政务，最干净的做法是**只用模型 cutoff 之后发布的政策**做 holdout；如果模型答对，且能给出正确引用，才说明 RAG 真的工作。

---

### 4.7 语料/覆盖度指标总表

| 指标 | 公式 | 分母 | 用途 | 陷阱 |
|---|---|---|---|---|
| `source_coverage` | `\vert ingested∩U\vert/\vert U\vert` | 目标全集 `U` | 接入进度 | 用城市数代替政策类型；分母不写清 |
| `corpus_recall` | `#Q with gold evidence in corpus/#Q_world` | `world_has=true` 的问题 | 语料是否够答 | 需要 gold doc；换快照要重算 |
| `no_policy_evidence_recall` | `#NO_POLICY with supporting docs/#NO_POLICY` | `NO_POLICY` 问题 | “无规定”结论是否有文件依据 | 不能当答案召回；需人工确认空白 |
| `retrieval_recall@k` | `#Q with gold in top-k/#Q_index` | gold 已入语料的问题 | 检索召回 | 语料缺失会伪装成检索失败 |
| `unjudged_rate` | `#unjudged retrieved/#retrieved` | 检索结果 | qrels 完整性 | 高则 recall 不可信 |
| `bpref` | 见 §4.2 | 已判相关/不相关 | 不完整判断下的稳健排序 | 仍依赖已判集合 |
| `no_source_rate` | `#failures with gold absent/#failures` | 失败样本 | 语料缺失诊断 | 需要 gold 和语料快照 |
| `retrieval_miss_rate` | 见 §4.4 | 有 gold 的问题 | 检索诊断 | 与 `no_source` 分开 |
| `reader_miss_rate` | 见 §4.4 | 召回了 gold 的问题 | 阅读器诊断 | 与检索分开 |
| `coverage_delta` | `source_coverage(t+1)-source_coverage(t)` | 两个快照 | 接入边际收益 | 必须同题集同模型 |
| `contamination_gap` | `closed_book_acc - rag_acc` | 评测集 | 污染/参数知识 | 负值也可能表示检索有害 |

---

### 4.8 对中文政务的适用性判断

**必须报：**

- `source_coverage`（城市级 + 城市×政策类型级，分开报）；
- `corpus_recall`（按 ANSWERABLE / NOT_COVERED 分层）；
- `no_policy_evidence_recall`（“无规定”结论能否在语料中找到支持文件）；
- `retrieval_recall@k` + `unjudged_rate` + `bpref`；
- `no_source_rate` / `retrieval_miss_rate` / `reader_miss_rate`；
- `coverage_delta` + `answer_delta`（接入前后同题集）；
- `contamination_gap` + `citation_only_rate`（至少对高风险题）。

**不能报：**

- “语料覆盖率 92.9%”而不写分母是“城市数”还是“政策格子数”；
- “文档数 100 万”当覆盖度；
- 只用 retrieval recall 证明语料完整；
- 用 RAGAS context recall 当语料覆盖；
- 跨语料快照比较模型分数而不报 `corpus_snapshot`。

**一句话结论：** 语料 completeness/coverage **几乎没有**被当成一阶指标；现有工作最多做到 retrieval recall（依赖 qrels）或答案级 missing rate（混因）。我们必须自己定义 `U`、`corpus_recall`、`no_source_rate`，并把“语料缺失”从模型失败里拆出来。否则，313/337 这种数字会被误读成“政策覆盖 92.9%”，而实际政策级覆盖可能低得多。

## 5. 可直接抄的做法（8 条）

下面每条都可以原样写进评测规范；括号里是“为什么”和“怎么验”。

### 5.1 拒答主指标固定为 `risk@coverage` 四点 + `AURC/E-AURC`

**规定：**

- 必须报 `risk@coverage = 0.3 / 0.5 / 0.7 / 0.9` 四个点，而不是只报一个 accuracy 或一个拒答率。
- 每个点必须带 `n_answered`、`k_errors`、Wilson 95% CI；`n<200` 时额外报 Clopper-Pearson 区间。
- `AURC` 用梯形积分；`E-AURC = AURC - AURC_oracle`；`AURC_oracle` 用测试集真实标签算，只作参考，不参与调参。
- 阈值只能在 dev 上定；test 只跑一次，且必须固定 `model_version/prompt_version/index_version`。

**报告格式：**

| coverage | n_answered | errors | selective risk | Wilson 95% CI |
|---|---|---|---|---|
| 0.3 | ... | ... | ... | ... |
| 0.5 | ... | ... | ... | ... |
| 0.7 | ... | ... | ... | ... |
| 0.9 | ... | ... | ... | ... |

**为什么：** accuracy 可以通过多拒答做高；单一拒答率可以通过多硬答做低；只有 risk–coverage 同时约束两者。**怎么验：** 用同一份输出重算四个点，检查 `n_answered` 与 coverage 对得上；用 bootstrap 重采样检查 CI 是否覆盖真值。

---

### 5.2 拒答必须分四类、澄清单列，并用四条硬断言做发布门禁

**规定：** 每个回答只能标一个：

```text
answer / refuse_not_covered / refuse_no_policy / refuse_out_of_scope / clarify
```

金标必须有 `in_scope`、`index_has`、`world_has`、`gold_doc_ids`、`as_of`、`region`。四条硬断言：

```text
false_not_covered_rate = P(refuse_not_covered | gold.in_scope=true ∧ gold.index_has=true)  = 0
false_no_policy_rate   = P(refuse_no_policy   | gold.in_scope=true ∧ ¬(gold.index_has=true ∧ gold.world_has=false)) = 0
fabricated_no_policy   = P(refuse_no_policy   | searched.n_docs=0)    = 0
answer_on_missing_rate = P(answer             | gold.index_has=false) = 0
```

**报告：** 每条断言报 `k/n`、精确二项 95% 上界、失败样例（至少 5 条）。只要有一条 >0，发布门禁不通过；不允许用平均分“抵消”。

**为什么：** 这四条分别对应“有证据却说没接入”“不满足 no_policy 金标却说查过没有”“没查却说查过”“没证据却硬答”；它们不是精度问题，是虚假陈述。**怎么验：** 从生产日志抽 100 条拒答，人工复核 `searched.n_docs/n_queries` 与 `gold.index_has/world_has`；四条各算一次。

---

### 5.3 每个拒答必须带 `searched`，且 `no_policy` 不能是“零检索”

**规定：** 拒答/`no_policy` 的输出必须包含：

```json
{
  "n_docs": 12,
  "n_queries": 3,
  "top_k": 20,
  "index_version": "cv-20261111-1",
  "corpus_snapshot": "sha256:...",
  "gold_doc_hit": false,
  "retrieval_miss": true
}
```

校验规则：

- `refuse_no_policy`：`n_docs>0` 且 `n_queries>0`；否则 `fabricated_no_policy`。
- `refuse_not_covered`：`n_queries>0`；并再分 `no_source` / `retrieval_miss`。
- `answer`：至少一条 `citation.doc_id` 能回查到 `corpus_snapshot` 里的原文；否则按“无引用回答”单列。
- `clarify`：不计入拒答率，单独报“澄清率”。

**为什么：** “没查就说没有”与“查了确实没有”在用户看来都是“拒答”，但前者是编造。**怎么验：** 随机抽 50 条 `no_policy`，检查 `n_docs` 对应的文档是否真的被展示/可回查；再抽 50 条 `not_covered`，检查 `n_queries` 是否>0。

---

### 5.4 校准必须报 ECE + Brier + 分层可靠性图，且置信度要在 dev 上选型

**规定：**

```text
ECE   = Σ_m (|B_m|/n) · |acc(B_m) - conf(B_m)|      # 默认 15 个等宽 bin；等频需注明
Brier = (1/n) Σ_i (p_i - y_i)^2
```

- 同时报 `ECE`、`Brier`、`reliability diagram`；只报 ECE 视为不完整。
- 按 `city/policy_type/question_type` 分层报 `ECE`，并报最大切片 `ECE_max_slice` 与该切片 n。
- 至少比较三种置信度：序列 logprob、口述置信度（verbalized）、`P(True)/P(IK)`；在 dev 上选，test 上只报。
- 温度缩放只能在 dev 上拟合；如果 test 上重新拟合，必须标“oracle calibration”，不能当系统能力。

**为什么：** ECE 是平均，可能被大量容易样本稀释；Brier 对极端错误更敏感；分层能暴露“整体校准好、某个城市全错”的问题。**怎么验：** 用 15/10/20 三种 bin 数重算，检查结论是否稳定；不稳定就说明样本不够，报 CI。

---

### 5.5 引用必须按 L1/L2/L3 三层报，不能只报“引用覆盖率”

**规定：** 每条规范性命中必须给 `doc_id/article/span/url/effective_from/jurisdiction/doc_type/version`；按三层算：

```text
CLR = #locatable / #citations
CSR = #claims with ≥1 sufficient citation / #claims
AC  = #applicable / #citations
CP  = #citations(relevant ∧ still-needed) / #citations
pad_ratio = citation_span_length / minimal_sufficient_span_length
misgrounded_rate = #(locatable=1 ∧ sufficient=1 ∧ applicable=0) / #citations
```

**门槛（可按业务调，但必须先写死）：**

- `CLR ≥ 0.98`：低于此，先修解析/链接，不谈语义；
- `CSR ≥ 0.90`：每个规范 claim 至少一条支撑；
- `AC ≥ 0.95`：版本/法域/主体/事项匹配；
- `CP ≥ 0.85`：引用不是堆出来的；
- `pad_ratio > 3` 的引用单列“整段照抄”比例；
- 每个维度人工抽检 ≥10%，Cohen's κ ≥0.6；低于 0.6 先修标注指南，不发布指标。

**为什么：** 一条引用可以“真实存在、文本支持、但引的是失效版本”；ALCE 只到 L2，RAGAS 只看 context 忠实，都会漏掉 L3。**怎么验：** 抽 100 条引用，三人独立标 L1/L2/L3，算 κ；再用元数据规则跑全量，人工只复核规则判“适用”的灰区。

---

### 5.6 语料覆盖必须写分母：城市级与“城市×政策类型”级分开报

**规定：**

```text
source_coverage_city   = #cities with any ingested source / #target_cities
source_coverage_cell   = #(city, policy_type) cells with source / #target_cells
corpus_recall          = #Q with gold evidence in corpus / #Q_world
                         # Q_world = {q : gold.world_has=true} = ANSWERABLE ∪ NOT_COVERED
no_policy_evidence_recall = #NO_POLICY Q with supporting docs in corpus / #NO_POLICY Q
retrieval_recall@k     = #Q with gold in top-k / #Q_index
                         # Q_index = {q : gold.index_has=true}
unjudged_rate          = #retrieved docs not in qrels / #retrieved docs
bpref                  = (1/R) Σ_r (1 - |n above r|/min(R,N))
```

- 不允许只写“覆盖率 92.9%”；必须写“城市级 313/337，政策格子级 x/y”。
- `corpus_recall` 的分母只含 `world_has=true`；`NO_POLICY` 不进分母，另报 `no_policy_evidence_recall`。
- 每次换语料快照，重算 `corpus_recall` 与 `coverage_delta`；报告里带 `corpus_snapshot`。
- `unjudged_rate > 20%` 时，`retrieval_recall` 标“不可信”，先补 qrels 或改报 bpref。
- 新接入城市必须做“同题集前后对比”，报 `coverage_delta` 与 `answer_delta`，不能只报“新增 N 个城市”。

**为什么：** 313/337 是城市级投入指标，不是政策级覆盖；一个城市“接入”可能只覆盖一类政策。**怎么验：** 随机抽 10 个“已接入”城市，检查 6 类政策是否都有 gold doc；抽 10 个“未接入”城市，人工核实 `world_has`，防止把“世界上没有”算成“没接入”。

---

### 5.7 统计检验按“题”聚类，不按“claim/token”独立同分布

**规定：**

- 系统对比用 **paired bootstrap**（按题重采样）或 **McNemar**（二分类对错）；不要用独立样本 t 检验。
- 每个指标报 `k/n` + 95% CI；零失败报单侧 Clopper-Pearson 上界（n=50 约 5.82%，n=100 约 2.95%）。
- 多切片比较做多重比较校正（Holm/BH），并报效应量（risk 差、coverage 差），不要只报 p 值。
- dev/test 严格分开；test 只用一次；如果多次看 test，必须标“test 已污染”。

**为什么：** 同一题的多个 claim、同一对话的多轮、同一城市的多个问题都相关；按 claim/token 算会把 CI 做得过窄。**怎么验：** 用题级 cluster bootstrap 重算 CI，与朴素 CI 比较；如果差很多，说明聚类必须做。

---

### 5.8 每个数字都要带“版本四元组”，报告要能复现

**规定：** 所有指标必须带：

```text
model_version / prompt_version / index_version / corpus_snapshot
```

外加 `judge_version`（LLM judge 的模型+prompt+温度）、`annotator_ids`、`annotation_guide_version`。报告必须包含：

- 原始输出（JSONL）+ 评分脚本 + 随机种子；
- 每条指标的 `k/n/CI`；
- 失败样例（至少每类 5 条，脱敏）；
- 一条可复现命令（或 notebook）；
- 变更日志：这次相对上次改了什么，哪个指标动了，为什么。

**为什么：** RAG 的分数对语料快照、prompt、judge 极其敏感；没有版本四元组，两个数不可比。**怎么验：** 让另一个人只按报告复现一个指标；如果复现不了，报告不合格。

---

## 6. 不要用的做法（5 条）

### 6.1 不要用单一“拒答率”作为发布指标

**反例：** 报告写“拒答率 18%，符合预期”。**为什么不行：** 这 18% 里可能同时包含“正确承认没接入”“正确说无规定”“该答不答”“没查就说没有”；四种结局的代价完全相反。一个系统可以通过“多拒答”把错误率压低，也可以通过“少拒答”把拒答率做漂亮。**替代：** 四类结局 + 四条硬断言 + `risk@coverage`。

### 6.2 不要把 XSTest/OR-Bench 的 over-refusal 混进 RAG 弃答

**反例：** 用 XSTest 的 250 条 safe prompts 算“RAG 拒答率”，声称“我们的政务 RAG 过度拒答 12%”。**为什么不行：** XSTest/OR-Bench 测的是安全对齐（“看起来敏感但其实安全”），不是“知识库有没有这条政策”。两者的人群、危害、修复动作都不同。**替代：** 安全侧单列 over-refusal；RAG 侧用 2×2 金标。**可以借** 它们的“按类型分解”方法，但不能借构念。

### 6.3 不要只报 accuracy/EM，不报 coverage；不要在 test 上调拒答阈值

**反例：** 论文表格只写“accuracy 87.3%”，或者“我们在 test 上把阈值调到 0.62，效果最好”。**为什么不行：** 前者隐藏了“答了多少”；后者把测试集当验证集，分数偏乐观，无法复现。**替代：** 报 `risk@coverage` 四点 + `AURC`；阈值在 dev 上定，test 只跑一次；如果必须看 test，标“探索性分析”，不进入结论。

### 6.4 不要把 URL 200/字符串命中当“引用可验证”

**反例：** 脚本检查 `requests.get(url).status_code == 200`，或者 `span in page_text`，就报“引用准确率 99%”。**为什么不行：** 200 只说明链接活着，不说明引文存在、支撑、适用；字符串命中可能是导航栏/版权声明/另一篇文章；页面改版后 `span` 可能被截断。**替代：** L1 用 `doc_id + 字符偏移 + 原文哈希` 做逐字校验；L2 用 NLI/人工；L3 用元数据规则（生效/失效/管辖/主体）。URL 只作展示，不作证据。

### 6.5 不要把未标定的 LLM judge 当引用金标，也不要把 pooled qrels 当绝对真值

**反例 A：** 用 GPT-4 判“引用是否支撑”，不报 prompt、不报 κ、不抽检，直接写“citation precision 0.91”。**为什么不行：** LLM judge 对“部分支撑/外推/时效”很敏感，未标定的 judge 可能系统性放水；ALCE precision 的人机 κ 也只有 0.525，说明这个任务本身就难。**替代：** 双人标注 200 条算 κ；judge 与人工一致率 <0.7 就重写指南/换模型；报 judge 版本与温度。

**反例 B：** 用单个检索器 pool 出 qrels，报 `Recall@10=0.93`，并声称“语料覆盖 93%”。**为什么不行：** pooling bias 会漏掉其他检索器能找到的文档；`Recall@10` 的分母是“已标注问题”，不是“世界上所有政策”。**替代：** 多路召回 + 人工补标；报 `unjudged_rate` 和 `bpref`；语料覆盖另算 `source_coverage/corpus_recall`。

## 7. 核验说明与待核清单

### 7.1 怎么核的

- **链接/元数据：** 通过 arXiv API、arXiv 论文 HTML、Semantic Scholar/Crossref 元数据、DOI 解析记录核对标题、作者、年份、arXiv ID/DOI；截至 **2026-11-12**。本环境直接 `curl https://doi.org/...` 对 3 个 DOI 返回 `000`（网络限制，非链接失效）；其标题/年份/作者改由 Semantic Scholar/Crossref 元数据核对，canonical DOI 保留。
- **数字：** 优先取论文摘要/表格/正文明确写出的数字；由公式推算的，文中标“公式推算”或“按公式计算”（例如 CRC 的 9 个错误、Wilson 区间、CP 上界）。
- **没有编数字：** 凡是摘要/正文没有明确给出、只在二手博客看到的数字，一律不写；改为“待核”。
- **2026 年论文：** arXiv:2604.12995、2609.17546、2609.35794、2610.28340 等是 2026 年预印本/会议论文；引用时按“预印本/会议论文”看待，不当作多年复现的稳定结论。
- **没有“链接待核”条目：** 本次保留的链接均做了存在性核验；下面的“待核”是**数字/口径**待核，不是链接待核。如果后续发现链接失效，再降级标注。

### 7.2 待核清单（写进报告时要保留“待核”或补实验）

| 项目 | 待核内容 | 当前处理 |
|---|---|---|
| DuRetrieval 规模 | HF 卡 `corpus 100,001 / queries 2,000` 与坊间“100k queries/12M passages”冲突 | 正文只按 HF 卡引用，标“规模待核”；不写 12M |
| DomainRAG 指标 | `GE` 的具体分制/提示词；六项能力是否使用同一 judge | 正文写 `EM/EMS/F1/ROUGE-L/GE`；`GE` 分制待核；不写统一总分 |
| POLIS-Bench 权重 | semantic similarity 与 accuracy 的聚合权重/阈值 | 正文写“dual metric，权重待核” |
| UAEval4RAG `Joint` 权重 | 论文正文的 `w1/w2` 是否固定 | 只写 `Joint=w1·Correctness+w2·Acceptable`，不写 0.7/0.3 |
| Legal warrant pilot | pilot 的具体样本量、标注一致性、指标 | 正文写“pilot 很小，指标未标准化” |
| ALCE “50%” | 摘要口径是“best models still lack complete citation support 50% of the time”；具体模型/数据集 | 正文按摘要引用，未扩展为所有模型 |
| Magesh “60%+ incomplete” | Ask Practical Law AI 的查询分母/判定口径 | 按论文报告写“60%+ 查询 incomplete”，不细拆 |
| AbstentionBench 规模 | 既有二手材料写 17 数据集/35k 题，arXiv v1 写 20 数据集/20 LLM | 正文按 arXiv v1 写 20/20，标“以 v1 为准” |
| CitaLaw 文档数 | “约 500,000”是论文口径还是约数 | 写“约 500k”，不写精确到个位 |
| AttrScore 81–83% | 是 overall accuracy 还是 attributable 类的准确率 | 写“总体 81–83%（论文报告）”，不拆类 |
| Chinese SimpleQA 分数 | 63.8/61.9 对应的模型/子集 | 写“o1-preview 63.8%、Doubao-pro-32k 61.9%”，不泛化 |

### 7.3 与配套文档的关系

同目录的 [`ABSTENTION-CITATION-EVAL.md`](ABSTENTION-CITATION-EVAL.md) 是更早的长文，偏“文献 + 方案”；本文件是“决策版”，补了三样东西：

1. **2×2 金标**（`index_has × world_has`）与四条硬断言；
2. **公式级指标表**（risk–coverage、ALCE、AIS、AttrScore、VeriScore、CLR/CSR/AC/CP、source_coverage/corpus_recall/bpref）；
3. **中文政务适用性判断**（哪些能抄、哪些不能抄、没有公开 benchmark 的地方明说）。

如果两文数字有冲突，以本文件的核验日期和“待核”标注为准；不要把两文的数字混着引用。

### 7.4 最小复现包（建议仓库结构）

```text
docs/research/METRICS-B-ABSTENTION.md          # 本文
eval/
  specs/
    refusal-2x2.schema.json                     # 金标字段
    citation.schema.json                        # 引用字段
  scripts/
    selective_risk.py                           # risk@coverage/AURC/ECE/Brier
    citation_audit.py                           # CLR/CSR/AC/CP/pad_ratio
    coverage_audit.py                           # source_coverage/corpus_recall/bpref
  reports/
    2026-11-12-baseline.json                    # 带版本四元组
```

报告 JSON 至少包含：

```json
{
  "model_version": "...",
  "prompt_version": "...",
  "index_version": "cv-20261111-1",
  "corpus_snapshot": "sha256:...",
  "judge_version": "...",
  "metrics": {
    "risk_at_coverage": {"0.3": {"k": 0, "n": 0, "ci": [0, 0]}},
    "false_not_covered_rate": {"k": 0, "n": 0, "upper95": 0},
    "clr": {"k": 0, "n": 0},
    "csr": {"k": 0, "n": 0},
    "ac": {"k": 0, "n": 0},
    "source_coverage_city": {"k": 313, "n": 337}
  },
  "failures": []
}
```

**最后一句：** 这份盘点最重要的不是“又多列了 30 篇论文”，而是把三个容易混的东西拆开了——**“没接入/没检索到”“世界上没有”“模型不知道”**。只要评测规范里还把这三个混成一个“拒答率”，后面的 citation、calibration、coverage 都只是装饰。
