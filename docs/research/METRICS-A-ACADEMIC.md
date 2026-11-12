# RAG 问答系统的评测指标：学术侧盘点（2020–2026）

> 面向：中文政务政策问答（ClauseLedger），以及任何"检索 → 生成 → 引用"链路的离线评测。
> 目标：把"RAG 好不好"拆成**可复算的指标**，并说清每个指标在原文里到底怎么用、在哪里会骗人。
> 核查口径：下列 arXiv abs 页与 HTML 全文均在 **2026-11-12** 实际抓取成功（HTTP 200）；数字优先取自论文表格/正文，不能确认的标 `待核`。venue 只写能确认的，不确定的标 `待核`，不硬编。
> 与 [`ABSTENTION-CITATION-EVAL.md`](ABSTENTION-CITATION-EVAL.md) 的分工：那篇讲**拒答的选择性预测**与**引用核验的工程实现**；本篇讲**指标本身**——定义、分母、原文数字、失效边界。两篇在 ALCE / RAGAS / AIS 处交叉引用，不重复推导。

**一句话结论**：RAG 评测的绝大多数指标只回答三个问题之一——**证据有没有被取回**（Recall@k / nDCG@10）、**答案有没有被证据支持**（faithfulness / citation precision）、**最终判断对不对**（EM / accuracy）。三者不能互相替代；判定型任务（输出 `satisfied/unsatisfied/unknown/not_applicable` + 条文引用）的主指标必须是**四类混淆矩阵 + 错判率**，其余指标只能当诊断项。

---

## 0. 读之前：三个容易混淆的层次

1. **benchmark ≠ metric**。BEIR、MTEB、RGB、CRAG、LongBench、∞Bench、RULER 是**数据集/基准**；nDCG@10、accuracy、effective length 才是**指标**。总表按论文列，落地时按指标选。
2. **检索指标 ≠ 生成指标**。Recall@k 高只说明"证据在候选里"，不说明模型用了它；faithfulness 高只说明"答案能从上下文推出来"，不说明检索到了对的条文。RAGChecker 的价值就在于把这两层拆开分别算。
3. **"原文主指标" ≠ "你应该用的主指标"**。本文每篇都写原文主指标；最后一节才讨论判定型任务该换什么。不要把 ALCE 的 citation recall 直接当法律适用性指标。

另一个贯穿全文的区分：**support（被支持）≠ correctness（正确）≠ attribution（归因到正确来源）**。一段话可以完全被某段上下文支持，却是对法条的误读；也可以正确，却引了不相干的段落。RAGAS 的 faithfulness 只测第一种，ALCE 的 citation precision 测第三种，二者都不是"判断正确"。

---

## 1. 总表

> 排序按层：检索 → 端到端 → 生成质量 → 归因引用 → 幻觉 → 多跳/长文。HotpotQA 早于 2020，作为多跳指标的定义来源保留，并在行内注明。

| 论文 | 年份 | 任务 | 主指标 | 次指标 | 关键数字 | 链接 |
|---|---|---|---|---|---|---|
| DPR（Dense Passage Retrieval） | 2020 | 开放域 QA 检索 | Top-20 retrieval accuracy | Top-1/5/50/100 accuracy | NQ 78.4 vs BM25 59.1；TriviaQA 79.4 vs 66.9 | [2004.04906](https://arxiv.org/abs/2004.04906) |
| BEIR | 2021 | 零样本异构检索基准 | nDCG@10 | MAP@100、Recall@100 | 18 数据集/10 系统；跨系统均值相对 BM25 从 -27.9% 到 +11% | [2104.08663](https://arxiv.org/abs/2104.08663) |
| ColBERTv2 | 2022 | 晚期交互检索 | nDCG@10（BEIR） | Success@5（LoTTE）、索引空间/延迟 | 22/28 OOD 任务超基线；空间降 6–10× | [2112.01488](https://arxiv.org/abs/2112.01488) |
| T2Ranking | 2023 | 中文段落检索/重排 | MRR@10、Recall@50、Recall@1K | 重排 nDCG@20/@100 | 307K 查询/2.3M 段落；BM25 Recall@50 0.4918 | [2304.03679](https://arxiv.org/abs/2304.03679) |
| MTEB（retrieval 子集） | 2023 | 文本嵌入多任务基准 | nDCG@10 | 8 类任务平均分 | 58 数据集/112 语言/33 模型 | [2210.07316](https://arxiv.org/abs/2210.07316) |
| FiD（Fusion-in-Decoder） | 2020 | 开放域 QA 生成 | EM | F1 | NQ 51.4 EM；TriviaQA 67.6 EM；SQuAD 56.7 EM | [2007.01282](https://arxiv.org/abs/2007.01282) |
| RETRO | 2021 | 检索增强语言建模 | bits-per-byte（bpb） | 下游困惑度/生成质量 | 2T token 库；25× 更少参数，Pile 上可比 GPT-3 | [2112.04426](https://arxiv.org/abs/2112.04426) |
| Self-RAG | 2023 | 自反思 RAG（QA/事实核查/长文） | accuracy + FactScore | citation precision/recall、ROUGE、MAUVE | 7B PopQA 54.9；ASQA citation P/R 66.9/67.8 | [2310.11511](https://arxiv.org/abs/2310.11511) |
| CRAG（Comprehensive RAG Benchmark） | 2024 | 端到端 RAG 基准 | accuracy、truthfulness | hallucination rate、missing rate | 4,409 QA；LLM-only ≤34%，朴素 RAG 44%，工业 RAG 63% 无幻觉 | [2406.04744](https://arxiv.org/abs/2406.04744) |
| RGB（Benchmarking LLMs in RAG） | 2023 | 中英 RAG 四能力 | accuracy | rejection rate、error detection/correction rate | 噪声 >80% 时 ChatGPT 96.33→76.00；负拒绝最高 45% | [2309.01431](https://arxiv.org/abs/2309.01431) |
| RAGChecker | 2024 | 细粒度诊断 RAG | 整体 precision/recall/F1 | 10 个检索/生成子指标 | 与人工 Pearson 61.93；RAGAS 48.31；人类上限 70.09 | [2408.08067](https://arxiv.org/abs/2408.08067) |
| RAGAS | 2023 | 无参考 RAG 评测 | faithfulness、answer relevance、context relevance | context precision/recall（2.x 库） | 与人工 pairwise 一致率 0.95/0.78/0.70 | [2309.15217](https://arxiv.org/abs/2309.15217) |
| ARES | 2023 | 轻量 judge + PPI 的 RAG 评测 | context relevance、answer faithfulness、answer relevance | 置信区间、AIS 幻觉率 | 比 RAGAS 高 59.3/14.4 个百分点；少 78% 标注 | [2311.09476](https://arxiv.org/abs/2311.09476) |
| RAGBench | 2024 | 大规模 RAG 评测基准 | TRACe（Utilization/Relevance/Adherence/Completeness） | 各分量相关性 | 100k 样本/5 领域；GPT-4 adherence F1 0.96 | [2407.11005](https://arxiv.org/abs/2407.11005) |
| CRUD-RAG | 2024 | 中文 RAG 四类任务 | ROUGE/BLEU/BERTScore/RAGQuestEval | 检索 recall/precision | 36,166 问；Create/Read/Update/Delete | [2401.17043](https://arxiv.org/abs/2401.17043) |
| AQA（Attributed QA） | 2022 | 带归因的 QA | attributable accuracy（AIS） | AutoAIS 与人工相关 | AIS 与 AutoAIS 系统级 Pearson 0.96；与 EM 仅 0.45 | [2212.08037](https://arxiv.org/abs/2212.08037) |
| ALCE | 2023 | 长文 QA + 引用 | citation recall、citation precision | correctness、fluency | ELI5 最好模型仍有 50% 缺完整支撑；κ=0.698/0.525 | [2305.14627](https://arxiv.org/abs/2305.14627) |
| AttrScore | 2023 | 归因三分类评测 | 每类 F1 + micro-F1（=accuracy） | micro-F1、跨领域 | GPT-4 81–83%；微调 FLAN-T5-770M 超 ChatGPT | [2305.06311](https://arxiv.org/abs/2305.06311) |
| RAGTruth | 2024 | RAG 幻觉语料/检测 | span-level F1 | response-level F1 | 近 18k 响应；span F1：QA 58.2、Writing 38.8 | [2401.00396](https://arxiv.org/abs/2401.00396) |
| FActScore | 2023 | 长文事实精度 | FActScore（原子事实支持率） | 与人工相关、自动估计误差 | InstructGPT 42%、ChatGPT 58%、PerplexityAI 71% | [2305.14251](https://arxiv.org/abs/2305.14251) |
| SelfCheckGPT | 2023 | 黑盒幻觉检测 | 句级 AUC-PR | 段落级 Pearson | WikiBio 1,908 句；NonFact AUC-PR 85.63 | [2303.08896](https://arxiv.org/abs/2303.08896) |
| HotpotQA | 2018 | 多跳 QA | EM/F1 | supporting fact EM/F1、joint EM/F1 | 113k 问；句级 supporting facts | [1809.09600](https://arxiv.org/abs/1809.09600) |
| MultiHop-RAG | 2024 | 多跳 RAG 检索/生成 | MAP@K、MRR@K、Hit@K + accuracy | 分查询类型 accuracy | 2,556 查询；GPT-4 检索块 0.56 vs 金标 0.89 | [2401.15391](https://arxiv.org/abs/2401.15391) |
| Lost in the Middle | 2023 | 长上下文位置消融 | accuracy by position | oracle/闭卷对照 | GPT-3.5 闭卷 56.1%、oracle 88.3%；中间低于闭卷 | [2307.03172](https://arxiv.org/abs/2307.03172) |
| LongBench | 2023 | 长文本多任务基准 | 任务相关 F1/ROUGE-L/Accuracy/LCC | 平均分 | 21 数据集/6 类/4,750 例；英均 6,711 词 | [2308.14508](https://arxiv.org/abs/2308.14508) |
| ∞Bench | 2024 | 100K+ 长上下文基准 | accuracy/EM | ROUGE-L-Sum（摘要） | 12 子任务；GPT-4 平均 45.63 | [2402.13718](https://arxiv.org/abs/2402.13718) |
| RULER | 2024 | 长上下文有效长度 | accuracy at length / effective length | 13 任务加权均分 | 17 模型；仅一半在 32K 达 85.6% 阈值 | [2404.06654](https://arxiv.org/abs/2404.06654) |

> 表注：BEIR 的 "18 数据集/10 系统" 是 benchmark 规模，不是指标；MTEB 的 33 模型是论文评测规模。HotpotQA 超出 2020–2026 窗口，仅作为 supporting-fact 指标定义来源；若严格限定年份，可把它放在"多跳"一节而不计入总表。

---

## 2. 检索层：先保证证据进得来

检索层的指标只关心一件事：**给定 query，相关证据有没有排在前面**。它不关心答案生成，也不关心引用格式。检索层最常见的错误是"用生成指标反推检索"——生成对了不代表检索对了，可能只是模型记住了。

### 2.1 DPR：把"检索对不对"变成 top-k 命中率

- **论文/年份**：Dense Passage Retrieval for Open-Domain Question Answering，2020（EMNLP 2020）。
- **任务与数据集**：开放域 QA 的段落检索；NQ、TriviaQA、WebQuestions、CuratedTREC、SQuAD。用 BM25 难负例 + in-batch 负例训练双塔。
- **主指标**：**Top-20 retrieval accuracy**——top-20 段落中任一段包含答案字符串即算命中。原文也报 top-1/5/50/100。
- **次指标**：Top-k accuracy 曲线；下游 reader 的 EM/F1（用来证明检索提升能传导）。
- **关键数字**：NQ 59.1→78.4、TriviaQA 66.9→79.4、WebQuestions 55.0→73.2、CuratedTREC 70.9→79.8（BM25 → DPR，top-20）。摘要称相对 BM25 绝对提升 9%–19%。
- **它解决什么评测问题**：第一次把"开放域 QA 的检索"从 BM25 的工程调参变成可复现的 dense retriever 对比；top-k accuracy 成为后续 RAG 检索的默认入门指标。
- **失效点**：①"包含答案字符串"很宽松，段落可能包含答案但语境错误；②只要求一个段落命中，不惩罚其他相关段落排得低；③不测多证据问题；④不同 tokenizer/答案别名会改变命中。**判定型任务不要直接用它**：法条检索需要"条款级 + 全部要件覆盖"，不是"答案字符串在 top-20"。

### 2.2 BEIR：零样本异构检索的 nDCG@10

- **论文/年份**：BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models，2021（NeurIPS 2021 Datasets & Benchmarks）。
- **任务与数据集**：18 个 IR 数据集，覆盖问答、事实核查、实体检索、新闻、生物医学等；10 个检索系统（BM25、dense、sparse、late-interaction 等）零样本评测。
- **主指标**：**nDCG@10**。原文同时报 MAP@100、Recall@100。
- **次指标**：各数据集 nDCG@10、相对 BM25 的平均变化。
- **关键数字**：BM25 是强基线；跨系统 "Avg. Performance vs. BM25" 从 **-27.9% 到 +11%**；TREC-COVID BM25 0.656 → BM25+CE 0.757；NQ 0.329→0.533；FEVER 0.753→0.819。
- **它解决什么评测问题**：把"某个模型在某个数据集上刷分"变成"跨 18 个异构任务是否稳定"；nDCG@10 成为稠密检索的事实标准。
- **失效点**：①多数数据集的 qrels 是**二元**的，nDCG 的"分级"优势用不上；②pooling 只对参与系统的结果做标注，未标注文档被当负例，新系统会被低估（TREC-COVID 后来补标注就是例子）；③nDCG@10 截断太短，长尾证据进不了前 10；④跨数据集分数不可直接平均——任务难度差异极大。**报告时必须写清 qrels 来源、标注完整性和 cutoff。**

### 2.3 ColBERTv2：晚期交互与 Success@5

- **论文/年份**：ColBERTv2: Effective and Efficient Retrieval via Lightweight Late Interaction，2022（NAACL 2022）。
- **任务与数据集**：BEIR、LoTTE、MS MARCO；用残差压缩把 token 级晚期交互的索引压小。
- **主指标**：**nDCG@10**（BEIR/MS MARCO）；LoTTE 用 **Success@5**（top-5 中至少一个相关）。
- **次指标**：索引空间、延迟、OOD 任务胜率。
- **关键数字**：BEIR 例：DBPedia 44.6、NQ 56.2、HotpotQA 66.7、FEVER 78.5、SciFact 69.3（nDCG@10）；22/28 OOD 测试优于基线；空间降 6–10×。
- **它解决什么评测问题**：证明"token 级交互"可以在可接受的存储/延迟下跑赢单向量；把效率纳入检索评测。
- **失效点**：①Success@5 只测"有没有一个相关"，不测排序质量；②BEIR 分数仍受 qrels 完整性影响；③索引大小/延迟依赖硬件，跨论文不可比；④对中文需要重新训练/分词，不能直接套英文结论。

### 2.4 T2Ranking：中文段落检索的 MRR/Recall

- **论文/年份**：T2Ranking: A Large-scale Chinese Benchmark for Passage Ranking，2023（SIGIR 2023）。
- **任务与数据集**：中文段落检索与重排；307K 查询、2.3M 段落（来自 1.8M 文档）、50K 测试查询；4 级人工相关性。
- **主指标**：检索 **MRR@10、Recall@50、Recall@1K**；重排 **MRR@10、nDCG@20、nDCG@100**。
- **次指标**：不同相关性等级（Level 2/3 视为相关）的分数、按查询类型拆分。
- **关键数字**：BM25 的 Recall@50 仅 **0.4918**，而同口径 MS-MARCO 约 **0.700**——中文段落检索明显更难。
- **它解决什么评测问题**：给中文检索一个大规模、四级相关性、带重排标注的基准；MRR 强调"第一个相关段落排多高"，Recall@50/@1K 强调"相关段落有没有进候选"。
- **失效点**：①中文分词/段落切分影响召回；②MRR@10 只看第一个相关，忽略其余相关段落；③四级相关性在计算 nDCG 时需要明确 gain（原文用 `2^rel-1` 还是 `rel` 要按论文，`待核`）；④测试集与训练集同分布，不能代表政务政策这种长条款、跨版本语料。

### 2.5 MTEB 的 retrieval 子集：嵌入模型的"体检表"

- **论文/年份**：MTEB: Massive Text Embedding Benchmark，2023（EACL 2023）。
- **任务与数据集**：8 类任务（retrieval、reranking、clustering、classification、pair classification、STS、summarization、bitext mining），58 数据集、112 语言、33 模型；retrieval 子集复用 BEIR，每类最多 15 个数据集。
- **主指标**：retrieval 用 **nDCG@10**；其他任务各用其惯用指标（accuracy、Spearman 等）。
- **次指标**：8 类任务平均分、按语言/领域拆分。
- **关键数字**：58 数据集/112 语言/33 模型；retrieval 子集复用 BEIR，具体数据集数按 BEIR 口径（不另造数）。
- **它解决什么评测问题**：把"嵌入模型好不好"从单一检索分数扩展成多任务画像；让同一个模型在 retrieval/STS/classification 上的取舍可见。
- **失效点**：①排行榜分数受数据集选择、提示/模板、归一化影响；②retrieval 子集与 BEIR 高度重叠，不是独立证据；③多语言覆盖不等于中文政务领域覆盖；④"平均分"会掩盖某个任务崩溃。**选 embedding 时看 retrieval 子集的分项，不要只看总平均。**

### 2.6 检索层小结：什么时候用哪个

| 场景 | 首选指标 | 分母/单位 | 不要用 |
|---|---|---|---|
| 单条证据、问"第一条对没对" | MRR@10 | query | nDCG@10（分级信息浪费） |
| 多条证据、问"相关段落排得好不好" | nDCG@10 | query | Accuracy（太粗） |
| 候选生成、问"证据有没有进 top-k" | Recall@k | query | Precision@k（候选多时天然低） |
| 开放域 QA、只要一个答案段 | Top-k accuracy | query | Recall@k（定义不同，易混） |
| 中文段落检索 | MRR@10 + Recall@50/@1K | query | 英文 BEIR 分数直接类比 |
| 选嵌入模型 | nDCG@10 + 分项 | query/dataset | 总平均分 |

**工程提醒**：检索指标的分母是 **query 数**，不是段落数；qrels 不完整时 Recall 会被低估，Precision 会被高估。报告时至少写：qrels 来源、相关性等级、cutoff、是否 pool 了新系统。

---

## 3. 端到端：从"证据"到"答案"

端到端论文的主指标通常是**任务准确率**，但它们真正有价值的是**诊断子指标**——把失败拆成检索失败、利用失败、幻觉、拒答失败。看这类论文，不要只看 SOTA 那一行。

### 3.1 FiD：多段落融合的 reader

- **论文/年份**： Leveraging Passage Retrieval with Generative Models for Open Domain Question Answering，2020（EACL 2021）。
- **任务与数据集**：开放域 QA；NQ、TriviaQA、SQuAD；每个问题检索多段，分别编码后由 decoder 融合。
- **主指标**：**EM**；同时报 F1。
- **次指标**：不同检索段落数（k）的 EM 曲线；oracle 检索 vs 实际检索。
- **关键数字**：NQ 51.4 EM、TriviaQA 67.6 EM、SQuAD 56.7 EM / 63.2 F1；检索段落增多时性能持续提升。
- **它解决什么评测问题**：证明"多段证据融合"比单段 reader 强，并把 reader 的上限与检索质量分开看（oracle 检索实验）。
- **失效点**：①FiD 假设给定段落相关，**不评测检索器**；②EM 对答案别名/格式敏感；③段落数增加带来成本，论文没有统一的成本归一化；④长答案/多跳问题 EM 偏低。

### 3.2 RETRO：检索增强语言模型的 bpb

- **论文/年份**：Improving Language Models by Retrieving from Trillions of Tokens，2021（ICML 2022，`待核`）。
- **任务与数据集**：语言建模；2T token 检索库；Pile 上评估。
- **主指标**：**bits-per-byte（bpb）**，等价于按字节归一化的交叉熵；越低越好。
- **次指标**：下游生成质量、参数效率、检索库规模消融。
- **关键数字**：2T token 库；25× 更少参数，Pile 上性能可比 GPT-3 与 Jurassic-1。
- **它解决什么评测问题**：把"检索增强"从 QA 扩展到通用语言建模；用 bpb 让不同 tokenizer/模型规模可比。
- **失效点**：①bpb 测的是语言建模，不是事实正确性；②检索到的内容可能被模型复制，bpb 下降不代表引用可靠；③没有归因/引用指标；④2T 库的构建与去重成本未计入评测。

### 3.3 Self-RAG：自反思 token 与引用指标

- **论文/年份**：Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection，2023（ICLR 2024，`待核`）。
- **任务与数据集**：开放域 QA（PopQA、TriviaQA、PubHealth、ARC、Bio）、长文生成（ASQA）；模型学习生成 retrieve/critique 反射 token。
- **主指标**：**accuracy**（各 QA 数据集）+ **FactScore**（长文事实性）。
- **次指标**：**citation precision/recall**（沿 ALCE 定义）、ROUGE、MAUVE、str-em。
- **关键数字**：7B：PopQA 54.9、TriviaQA 66.4、PubHealth 72.4、ARC 67.3、Bio 81.2；ASQA citation P/R 66.9/67.8。13B：55.8/69.3/74.5/73.1/80.2；citation P/R 70.3/71.3。
- **它解决什么评测问题**：把"要不要检索、检索到的段落相不相关、生成有没有被支持"变成模型内部的可监督信号；同时用 citation P/R 约束归因。
- **失效点**：①反射 token 的训练标签来自 GPT-4 蒸馏，评测会继承 teacher 的偏好；②citation P/R 仍是 NLI/字符串匹配，法律推理的"隐含支持"测不到；③accuracy 与 citation 指标可能背离——答案对但引用错，或引用对但答案错；④自评可能自我一致（self-consistency）而非真实正确。

### 3.4 CRAG：端到端基准与 truthfulness

- **论文/年份**：Comprehensive RAG Benchmark（CRAG），2024（NeurIPS 2024 Datasets & Benchmarks）。
- **任务与数据集**：4,409 个 QA，5 个领域（finance、sports、music、movie、open）、8 种问题类型；含 mock API 与网页检索；评估 LLM-only、朴素 RAG、工业 RAG。
- **主指标**：**accuracy** 与 **truthfulness**。每个答案判 `accurate / acceptable / missing / incorrect`，分别记 **1 / 0.5 / 0 / -1**；truthfulness 是逐题分数的均值（惩罚幻觉，宁可 missing）。
- **次指标**：**hallucination rate**、**missing rate**；两个 LLM judge（ChatGPT、Llama-3-70B）取平均，人工抽检。
- **关键数字**：LLM-only 最高 34%，朴素 RAG 44%，工业 RAG 仅 **63%** 的回答无幻觉；Llama3-70B LLM-only 32.3 acc / 28.9 hall / 38.8 miss / 3.4 truth；GPT-4 Turbo 33.5/13.5/53.0/20.0；Task3 Llama3 40.6/31.6/27.8/9.1，GPT-4 Turbo 43.6/30.1/26.3/13.4。
- **它解决什么评测问题**：把"RAG 在真实动态网页上到底行不行"做成可复现基准；用 truthfulness 的负分刻度显式惩罚"编得像真的"。
- **失效点**：①LLM judge 有位置/长度/自我偏好，两个 judge 平均只是缓解；②truthfulness 的 -1 刻度是**人为权重**，换权重排名会变；③mock API/网页是冻结快照，时效性有限；④"missing"与"unknown"在业务上可能不是一回事，不能直接映射到判定型任务。

### 3.5 RGB：四种能力与拒答率

- **论文/年份**：Benchmarking Large Language Models in Retrieval-Augmented Generation，2023（AAAI 2024）。
- **任务与数据集**：中英双语；600 基础题 + 200 信息整合 + 200 反事实；6 个 LLM；四能力：噪声鲁棒、负拒绝、信息整合、反事实鲁棒。
- **主指标**：**accuracy**（生成文本包含标准答案即算对，子串匹配）。
- **次指标**：**rejection rate**（只给噪声文档时输出固定拒答句）、**error detection rate**、**error correction rate**。
- **关键数字**：噪声比例 >80% 时 ChatGPT 96.33→76.00、ChatGLM2 91.33→57.33；负拒绝最高仅 45%（英）/43.33%（中）；信息整合无噪声最高 60%/67%，加噪后 43%/55%。
- **它解决什么评测问题**：第一次系统区分"抗噪""该拒答时拒答""多文档整合""被错误前提带偏"四种能力；拒答率是 RAG 安全评测的早期形态。
- **失效点**：①accuracy 用子串匹配，措辞/语言变化会误判；②rejection 只认固定拒答句，换一种拒答表达就不算；③"负拒绝"只测"全噪声"场景，不测"部分证据 + 缺失条件"；④样本量 600/200/200，子组置信区间很宽；⑤反事实任务的"错误前提"是人工构造的，不一定覆盖真实政策冲突。

### 3.6 RAGChecker：claim 级诊断

- **论文/年份**：RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation，2024。
- **任务与数据集**：8 个 RAG 系统、10 个领域、280 条人工 meta-eval；用 claim 级抽取 + 自然语言推理，把整体指标拆成检索与生成两组。
- **主指标**：**整体 precision / recall / F1**（claim 级）。
- **次指标**：检索侧 **claim recall**、**context precision**；生成侧 **faithfulness**、**context utilization**、**relevant noise sensitivity**、**irrelevant noise sensitivity**、**hallucination**、**self-knowledge**（后几项越低越好）。
- **关键数字**：与人工评分的整体 Pearson **61.93**，RAGAS 48.31，人类上限 70.09；检索 claim recall 与 context precision 能定位"检索错"还是"生成错"。
- **它解决什么评测问题**：把"RAG 错了"从单一分数变成**可归因的诊断树**：是没召回，还是召回噪声大，还是生成了上下文没有的内容，还是模型靠参数知识答对/答错。
- **失效点**：①claim 抽取本身是 LLM，会漏/并/拆错；②NLI 对法律、数字、否定、条件句不稳健；③"self-knowledge"把"不靠上下文也答对"算作问题，但在判定型任务里可能正是需要的（规则推理）；④280 条 meta-eval 规模有限，子领域结论不稳；⑤成本高，不适合每次 CI 全跑。

### 3.7 端到端小结：benchmark 分数怎么读

| 论文 | 分数高说明什么 | 不说明什么 |
|---|---|---|
| FiD | reader 能用多段证据答题 | 检索器好不好、引用对不对 |
| RETRO | 检索增强能降语言建模损失 | 事实正确、归因可靠 |
| Self-RAG | 模型会决定检索并自评 | 自评是否可信、法律适用是否正确 |
| CRAG | 在冻结网页快照上的端到端正确率 | 真实时效、条文级引用 |
| RGB | 抗噪/拒答/整合/反事实四能力 | 细粒度归因、多跳推理 |
| RAGChecker | 能定位检索/生成哪一层出错 | 绝对正确率、跨领域泛化 |

---

## 4. 生成质量：faithfulness、relevance 与 TRACe

这一层的共同点：**不要求标准答案，而要求"答案与上下文/问题"的关系可判定**。它们适合做回归测试和错误分析，不适合单独当"正确率"。

### 4.1 RAGAS：三个无参考指标

- **论文/年份**：RAGAS: Automated Evaluation of Retrieval Augmented Generation，2023（EACL 2024 Demo，`待核`）。
- **任务与数据集**：无参考 RAG 评测；WikiEval（50 例）人工标注；三个指标分别测生成对上下文的忠诚、答案对问题的相关、上下文对问题的相关。
- **主指标**：
  - **Faithfulness** = 被上下文支持的陈述数 / 答案总陈述数。机制：LLM 把答案拆成陈述，再逐条判是否被给定上下文蕴含。
  - **Answer Relevance** = 由答案反向生成 N 个问题，与原问题算平均余弦相似度。机制：答案越能"反推出"原问题，越相关。
  - **Context Relevance** = 上下文中与问题相关的句子数 / 总句子数。机制：LLM 逐句判相关。
- **次指标**：RAGAS 2.x 库另加 **Context Precision**（逐 k 的 precision 对相关块取平均）与 **Context Recall**（参考陈述被上下文支持的比例）；这两个不是 2023 论文的主指标，引用时要区分版本。
- **关键数字**：与人工 pairwise 一致率 **0.95 / 0.78 / 0.70**（faithfulness / answer relevance / context relevance）；GPT Score 为 0.72/0.52/0.63。
- **它解决什么评测问题**：第一次让"没有金标准答案"的 RAG 也能算分；三个指标分别对应"别瞎编""别答非所问""别拿无关段落"。
- **失效点**：①**faithfulness 不评引用归属**——它只看答案是否被"提供的上下文"支持，不看你引的是哪一段；上下文里有两段互相矛盾时，答案支持其中一段就算高。②context relevance 是句子比例，短上下文（只放一句相关句）容易刷高，且不测"证据是否充分"。③answer relevance 只看语义相似，答案可以相关但错误。④LLM judge 有自我偏好/长度偏差；⑤陈述拆分错误会直接改变分母。**RAGAS 2.x 的 context precision/recall 需要参考文档/参考陈述，已不是纯粹无参考。**

### 4.2 ARES：小样本 + PPI 的 judge

- **论文/年份**：ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems，2023（NAACL 2024，`待核`）。
- **任务与数据集**：8 个知识密集任务（KILT、SuperGLUE、AIS）；三维：context relevance、answer faithfulness、answer relevance；用合成数据微调轻量 judge，再用 **prediction-powered inference（PPI）** 加少量人工标注给置信区间。
- **主指标**：三个维度的 judge 分数（分类器概率校准后）。
- **次指标**：与人工偏好的准确率、AIS 幻觉率误差、所需人工标注量。
- **关键数字**：约 **150** 条人工偏好数据即可；比 RAGAS 在 context/answer relevance 上平均高 **59.3/14.4 个百分点**；AIS 幻觉率预测误差在 **2.5pp** 内；比传统人工评测少 **78%** 标注。
- **它解决什么评测问题**：把"LLM judge 不可信"变成"用 PPI 给 judge 加统计保证"；小样本也能报置信区间。
- **失效点**：①PPI 的前提是 judge 在抽样分布上与人工标注可交换，领域偏移会破坏；②合成训练数据继承生成模型的偏差；③三个维度仍是粗粒度，不测条文级归因；④"少 78% 标注"是相对其基线，不是绝对零标注。

### 4.3 RAGBench 与 TRACe：把上下文利用拆成四段

- **论文/年份**：RAGBench: Explainable Benchmark for Retrieval-Augmented Generation Systems，2024（venue `待核`）。
- **任务与数据集**：100k 样本、5 个行业领域；每个样本带检索上下文、答案、支撑标注。
- **主指标**：**TRACe** 四个分量：
  - **Relevance** = 上下文中相关 token 数 / 上下文总 token 数。
  - **Utilization** = 被答案使用的 token 数 / 上下文总 token 数。
  - **Completeness** = 被使用的相关 token 数 / 相关 token 总数。
  - **Adherence** = 答案是否完全被上下文支持（布尔/词级支持率）。
- **次指标**：各分量与人工判决的相关性；GPT-4 作为标注器的 agreement。
- **关键数字**：100k 样本/5 领域；GPT-4 标注与人工 adherence **F1 0.96、准确率 0.93**。
- **它解决什么评测问题**：把"上下文有没有用上"从单一 faithfulness 拆成"相关没""用了没""用全没""是否越界"；能区分"检索到但没用"和"没用对"。
- **失效点**：①token 级相关/使用标注依赖分词与对齐；②Adherence 是"全部被支持"的布尔，长答案很容易被判 0，区分度低；③TRACe 不测引用到具体条文；④100k 样本来自行业语料，中文政务不在分布内。

### 4.4 CRUD-RAG：中文 RAG 的四类任务

- **论文/年份**：CRUD-RAG: A Comprehensive Chinese Benchmark for Retrieval-Augmented Generation of Large Language Models，2024（venue `待核`）。
- **任务与数据集**：中文 36,166 问；Create/Read/Update/Delete 四类（创作、问答、改写、删除/去噪）。
- **主指标**：**ROUGE、BLEU、BERTScore、RAGQuestEval**（按任务选）。
- **次指标**：检索 recall/precision；不同文档类型/噪声比例。
- **关键数字**：36,166 问；四类任务覆盖生成与检索；RAGQuestEval 用"答案反生成问题"再比。
- **它解决什么评测问题**：给中文 RAG 一个多任务基准，避免只用英文 BEIR/ALCE 推断中文表现。
- **失效点**：①ROUGE/BLEU 对中文政策文本的改写/同义表达极不敏感；②BERTScore 测语义相似，不测法律正确；③RAGQuestEval 仍依赖 LLM 生成问题，成本与偏差；④四类任务与"判定型输出"不同，不能直接迁移。

### 4.5 生成质量指标怎么选

| 需求 | 指标 | 需要什么 | 主要盲区 |
|---|---|---|---|
| 答案是否瞎编 | RAGAS faithfulness / TRACe Adherence | 上下文 + 答案 | 不评引用归属；矛盾上下文 |
| 答案是否答非所问 | RAGAS answer relevance | 问题 + 答案 | 相关但错误 |
| 上下文是否相关 | RAGAS context relevance / TRACe Relevance | 问题 + 上下文 | 句子比例可刷分 |
| 上下文是否用上 | TRACe Utilization/Completeness | 答案 + 上下文 | token 对齐 |
| 中文生成质量 | CRUD-RAG 的 ROUGE/BERTScore | 参考文本 | 语义正确但措辞不同 |
| 小样本 + 置信区间 | ARES + PPI | 少量人工偏好 | 分布偏移 |

---

## 5. 归因与引用：从"说得对"到"引得住"

这一层回答：**答案里的每个陈述，能不能被它引用的那段原文支持？** 注意三个不同的概念：

- **attribution / AIS**：输出是否"可归因"到给定证据（通常要求完全支持 + 可解释）。
- **citation recall**：有多少陈述被"至少一个引用段落"完全支持。
- **citation precision**：引用的段落里，有多少真的支持该陈述。

三者都不是"法律适用是否正确"，只是"文本支持"。

### 5.1 AQA：把 AIS 变成可算的 attributable accuracy

- **论文/年份**：Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models，2022（venue `待核`）。
- **任务与数据集**：给定问题，模型输出答案 + 一个证据段落；人工标注 AIS（Attributable to Identified Sources）作为金标；提出 AutoAIS（NLI 自动判）。
- **主指标**：**attributable accuracy**——输出被判为"完全被引用段落支持且可解释"的比例（AIS）。
- **次指标**：AutoAIS 与人工 AIS 的系统级相关；与 EM/F1 的对比。
- **关键数字**：人工 AIS 与 AutoAIS 的系统级 Pearson **0.96**；与 EM 仅 **0.45**。
- **它解决什么评测问题**：证明"答案对"和"有归因"是两件事；EM 高的系统 AIS 不一定高，反之亦然。
- **失效点**：①AIS 要求"完全支持"，对部分支持/多段组合支持不友好；②AutoAIS 是 NLI，对隐含推理/法律条件句弱；③任务通常只允许一个引用段落，真实 RAG 常需多段；④"可解释"由人判，主观性高。

### 5.2 ALCE：citation recall / precision 的标准定义

- **论文/年份**：Enabling Large Language Models to Generate Text with Citations，2023（EMNLP 2023）。
- **任务与数据集**：长文 QA + 引用；ASQA、QAMPARI、ELI5；检索器给段落，模型生成带 `[1][2]` 引用的答案。
- **主指标**：
  - **Citation Recall** = 被引用段落**完全支持**的陈述数 / 总陈述数。分母是陈述，不是引用。
  - **Citation Precision** = 真正支持对应陈述的引用数 / 总引用数。分母是引用，不要求"最小引用集"。
- **次指标**：**correctness**（答案对不对）、**fluency**（语言质量）。
- **关键数字**：ELI5 上最好模型仍有 **50%** 的生成缺乏完整引用支撑；人工一致性 κ=**0.698**（recall）/ **0.525**（precision）；对人工判定的准确率 **85.1% / 77.6%**。
- **它解决什么评测问题**：把"引用"从格式检查变成可复算的召回/精确率；首次大规模显示"模型会引用，但引用常常不支持陈述"。
- **失效点**：①citation precision 不惩罚冗余引用——把相关段落全引上，precision 可能仍高；②NLI 判"完全支持"对数字/否定/条件句不稳；③陈述切分改变分母；④不评"引用的是否是权威/最新版本"；⑤ASQA/ELI5 是英文长文，中文政策需要重新标注。**判定型任务要把它改造成"条文级 + 要件级"，不能直接用段落级。**

### 5.3 AttrScore：三分类归因

- **论文/年份**：Automatic Evaluation of Attribution by Large Language Models，2023（EMNLP Findings 2023）。
- **任务与数据集**：12 个领域（New Bing 查询日志构造）；每个"陈述 + 引用"判三类：
  - **attributable**：引用完全支持陈述；
  - **extrapolatory**：引用信息不足，陈述超出引用；
  - **contradictory**：陈述与引用矛盾。
- **主指标**：**每类 F1 + micro-F1**（三分类；论文明确说 micro-F1 等价于 overall accuracy）。
- **次指标**：prompt 与微调两种设定；跨领域泛化。
- **关键数字**：GPT-4 在 GenSearch 上 **81–83%**，最高约 **85%**；微调 FLAN-T5-770M 超过 ChatGPT；Vicuna 零样本 34.6/41.4 → 微调 66.0/71.3（两列具体指标 `待核`）。
- **它解决什么评测问题**：把"引用支持"从二分类扩展为"支持/外推/矛盾"，能区分"引了但不充分"与"引了但相反"。
- **失效点**：①三分类仍是文本支持，不判法律适用；②训练数据由 GPT-4 合成，继承其偏差；③extrapolatory 与 contradictory 边界主观；④micro-F1 会被大类主导，需同时报每类 F1。

### 5.4 引用指标家族对照

| 指标 | 判定单位 | 分母 | 回答的问题 | 典型论文 |
|---|---|---|---|---|
| AIS / attributable accuracy | 输出 | 输出数 | 是否完全被引用证据支持且可解释 | AQA |
| AutoAIS | 输出 | 输出数 | NLI 自动估计 AIS | AQA |
| Citation Recall | 陈述 | 陈述数 | 多少陈述有至少一个引用完全支持 | ALCE、Self-RAG |
| Citation Precision | 引用 | 引用数 | 多少引用真的支持对应陈述 | ALCE、Self-RAG |
| AttrScore | 陈述-引用对 | 对数 | 支持/外推/矛盾三分类 | AttrScore |

**一句话**：citation recall 高但 precision 低 = "引得多、错得多"；precision 高但 recall 低 = "只敢引最稳的、很多陈述没引"。两个必须一起报，且要报分母。

---

## 6. 幻觉检测：不支持 ≠ 错误

幻觉检测的关键区分：

- **factual error**：陈述与世界事实不符；
- **unsupported claim**：陈述不被给定上下文支持（但可能世界事实为真）；
- **misattribution**：陈述被错误地归因到某来源（来源可能真实存在）。

RAG 场景最常发生的是第二、三种；只测第一种会漏掉"引对段落但用错"的情况。

### 6.1 RAGTruth：词级幻觉语料与 span F1

- **论文/年份**：RAGTruth: A Hallucination Corpus for Developing Trustworthy Retrieval-Augmented Language Models，2024（venue `待核`）。
- **任务与数据集**：近 **18,000** 条 RAG 响应，覆盖 QA、数据到文本、摘要、写作；词级人工标注四类幻觉：
  - Evident Conflict（明显与上下文冲突）
  - Subtle Conflict（细微冲突）
  - Evident Baseless Info（明显无依据）
  - Subtle Baseless Info（细微无依据）
- **主指标**：**span-level F1**（幻觉片段级）；同时报 **response-level F1**（整条响应是否有幻觉）。
- **次指标**：按任务拆分；微调检测器 vs GPT-4 prompt。
- **关键数字**：span F1：QA **58.2**、D2T **53.5**、Writing **38.8**、Sum **52.7**；response F1：68.2、88.1、59.1、78.7（按任务列，非 overall）；微调 Llama-2-13B 可媲美 GPT-4 prompt。
- **它解决什么评测问题**：给 RAG 幻觉一个**词级、分类型、跨任务**的标注集；让"检测器能不能定位到具体片段"可复算。
- **失效点**：①span F1 对边界敏感，写作任务只有 38.8，说明长文幻觉定位很难；②四类边界（尤其 subtle）主观；③检测 ≠ 修正；④语料主要是英文，中文政策需重标；⑤response-level F1 会被长响应稀释。

### 6.2 FActScore：原子事实支持率

- **论文/年份**：FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation，2023（EMNLP 2023）。
- **任务与数据集**：传记长文生成；把生成拆成**原子事实**（一个事实一个短句），逐条查可靠知识源是否支持。
- **主指标**：**FActScore** = 被支持的原子事实数 / 原子事实总数。
- **次指标**：与人工评分的相关；自动估计误差；成本。
- **关键数字**：InstructGPT **42%**、ChatGPT **58%**、PerplexityAI **71%**；自动估计误差 **<2%**；评估 6,500 条生成，人工标注需 **$26K**。
- **它解决什么评测问题**：把"长文事实性"从整体打分变成原子事实级精度；支持细粒度错误定位。
- **失效点**：①原子事实拆分本身是模型，拆错会改变分母；②知识源有 cutoff/版本，"当时对、现在错"会被误判；③传记领域的事实密度/风格与政策问答差异大；④"被知识源支持"不等于"被引用条文支持"；⑤成本仍高，不适合每次 CI。

### 6.3 SelfCheckGPT：采样一致性

- **论文/年份**：SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models，2023（EMNLP 2023）。
- **任务与数据集**：WikiBio 传记生成；238 篇、1,908 句；对同一 prompt 采样多条，比较句子间一致性。
- **主指标**：**句级 AUC-PR**（用一致性分数判幻觉）。
- **次指标**：段落级 Pearson 相关；不同一致性度量（unigram、BERTScore、NLI）。
- **关键数字**：句级 AUC-PR：NonFact **85.63**、NonFact* **41.04**、Factual **58.47**；段落级 Pearson 64.71（unigram max）/64.91（NLI）。
- **它解决什么评测问题**：零资源、黑盒、不需要外部知识库；用"模型自己多次回答是否一致"当幻觉信号。
- **失效点**：①**一致性 ≠ 事实性**——模型可以稳定地错；②采样温度/数量改变分数；③成本是 N 倍生成；④对"每次措辞不同但事实相同"会误判；⑤AUC-PR 依赖正负样本比例，跨数据集不可比。

### 6.4 幻觉指标对照

| 指标 | 需要什么 | 测什么 | 不测什么 |
|---|---|---|---|
| RAGTruth span F1 | 人工词级标注 | 幻觉片段定位 | 修正、法律适用 |
| FActScore | 可靠知识源 | 原子事实支持率 | 引用归属、上下文支持 |
| SelfCheckGPT AUC-PR | 多次采样 | 自一致性 | 真实事实、外部证据 |
| RAGAS faithfulness | 上下文 | 上下文支持 | 引用归属、世界事实 |
| ALCE citation precision | 引用段落 | 引用是否支持陈述 | 引用是否权威/最新 |

---

## 7. 多跳与长文：证据链与位置偏差

### 7.1 HotpotQA：supporting fact 指标的源头

- **论文/年份**：HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering，2018（EMNLP 2018；早于本文窗口，作为定义来源）。
- **任务与数据集**：113k 多跳问题；每个问题带句级 supporting facts；distractor 设定含干扰段落。
- **主指标**：**答案 EM / F1**。
- **次指标**：**supporting fact EM / F1**（句级）；**joint EM / F1**（答案与支撑事实都对的联合指标；EM 需两者都 1，F1 为乘积）。
- **关键数字**：113k 问；句级支撑标注；joint 指标显著低于答案单独指标（具体分数随模型/设定变化，`待核`）。
- **它解决什么评测问题**：第一次把"答案对不对"与"证据找没找对"分开，并要求两者联合正确；supporting fact 成为多跳/RAG 证据评测的标准形式。
- **失效点**：①supporting fact 是**句子级**，不是推理链/要件级；②joint 指标很苛刻，答案对但证据句选偏就归零；③distractor 设定与真实检索分布不同；④多跳推理的错误可能发生在"连接"而非"找句"，指标定位不到。

### 7.2 MultiHop-RAG：检索与生成分开测

- **论文/年份**：MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-Hop Queries，2024（venue `待核`）。
- **任务与数据集**：2,556 条多跳查询；四类：Inference 856（33.49%）、Temporal 583（22.81%）、Null 301（11.78%）等；知识库为新闻文章。
- **主指标**：检索 **MAP@K、MRR@K、Hit@K**（K=4/10）；生成 **accuracy**（GPT-4 判）。
- **次指标**：按查询类型拆分；检索块 vs 金标证据的对照。
- **关键数字**：GPT-4 用检索块 **0.56**、用金标证据 **0.89**；Llama-2-70B 0.28/0.32；Mixtral 0.32/0.36。
- **它解决什么评测问题**：把多跳 RAG 的"检索不到"与"推理不会"分开；Null 查询测"该拒答/该说不知道"。
- **失效点**：①Hit@K 只说明证据在 top-K，不说明顺序；②MAP/MRR 需要多证据标注，标注成本高；③accuracy 由 GPT-4 判，二值化掩盖部分正确；④新闻语料与政策条文的结构差异大；⑤"多跳"主要是实体/时间推理，不是条件适用推理。

### 7.3 Lost in the Middle：位置消融

- **论文/年份**：Lost in the Middle: How Language Models Use Long Contexts，2023（TACL 2023）。
- **任务与数据集**：多文档 QA、键值检索；20/30 篇文档，把答案放在首/中/尾；对比闭卷与 oracle。
- **主指标**：**accuracy by position**（按答案位置分组的准确率）。
- **次指标**：闭卷准确率、oracle 准确率、U 形曲线。
- **关键数字**：GPT-3.5-Turbo 闭卷 **56.1%**、oracle **88.3%**；答案在中间时低于闭卷。
- **它解决什么评测问题**：证明"上下文越长越好"是错的；模型对中间位置的信息利用最差，RAG 的段落排序/位置策略需要单独评。
- **失效点**：①合成位置消融，真实检索的段落相关性/冗余与实验不同；②只测一个答案位置，不测多证据分散；③模型/提示变化会改变曲线；④accuracy 仍是端到端，不定位"没看到"还是"看到了没用"。

### 7.4 LongBench：多任务长文本

- **论文/年份**：LongBench: A Bilingual, Multitask Benchmark for Long Context Understanding，2023（ACL 2024）。
- **任务与数据集**：21 个数据集、6 类任务（单文档 QA、多文档 QA、摘要、few-shot、合成、代码）；4,750 测试例；英均 6,711 词、中均 13,386 字。
- **主指标**：按任务：**F1**（QA/few-shot）、**ROUGE-L**（摘要）、**Accuracy(CLS/EM)**（分类/合成）、**LCC**（代码编辑相似度）。
- **次指标**：6 类任务平均、按长度/语言拆分。
- **关键数字**：21 数据集/4,750 例；英均 6,711 词、中均 13,386 字；任务间分数差异大，平均分不能替代分项。
- **它解决什么评测问题**：把"长上下文"从单一 needle 测试扩展为多任务；中英双语，能看语言差异。
- **失效点**：①"长"只有约 6.7k 词，不是 100K+；②任务指标不统一，平均分权重主观；③F1/ROUGE-L 对中文政策文本不敏感；④数据 contamination 风险；⑤不测检索质量/引用。

### 7.5 ∞Bench：100K+ 的 12 个子任务

- **论文/年份**：∞Bench: Extending Long Context Evaluation Beyond 100K Tokens，2024（ACL 2024）。
- **任务与数据集**：12 个子任务：En.Sum、En.QA、Zh.QA、En.MC、En.Dia、Code.Debug、Code.Run、Math.Calc、Math.Find、Retrieve.PassKey/Number/KV；平均长度 100K+ token。
- **主指标**：**accuracy / EM**（QA、检索、代码、数学等）；摘要用 **ROUGE-L-Sum**。
- **次指标**：按任务/语言拆分；与短上下文基线对比。
- **关键数字**：GPT-4 平均 **45.63**、Claude **37.06**、Kimi **34.73**、YaRN **19.96**；Code.Debug 直接答 **15.74%**，先复述相关代码后 **39.59%**。
- **它解决什么评测问题**：把长上下文评测推到 100K+，并覆盖摘要、代码、数学、检索等非 QA 任务；揭示"长上下文模型在复杂任务上仍很弱"。
- **失效点**：①部分任务（PassKey）是合成检索，容易饱和/被针对性训练；②ROUGE-L-Sum 与人工摘要质量弱相关；③任务间难度差异大，平均分不可比；④数据 contamination；⑤不测 RAG 的检索/引用链路。

### 7.6 RULER：有效长度与阈值

- **论文/年份**：RULER: What's the Real Context Size of Your Long-Context Language Models?，2024（COLM 2024）。
- **任务与数据集**：17 个长文模型、13 个任务、4 类（检索、多跳追踪、聚合、QA）；每任务每长度 500 例；长度 4K–128K。
- **主指标**：**accuracy at length**；由此定义 **effective length**——仍能达到阈值（Llama2-7B@4K 的 **85.6%**）的最大长度。
- **次指标**：13 任务加权均分（wAvg）；按任务类型拆分。
- **关键数字**：17 模型/13 任务；仅一半模型在 32K 仍达标；Llama2-7B@4K 阈值 85.6%。
- **它解决什么评测问题**：把"支持 128K"从营销数字变成"在什么长度上还能保持 85.6% 基线"的可复算指标。
- **失效点**：①阈值 85.6% 是人为选的（取 Llama2-7B@4K），换阈值排名会变；②任务集偏合成，真实长文（政策文件、合同）分布不同；③"effective length"是单点，不展示退化曲线；④不测检索/引用。

### 7.7 多跳/长文小结

| 问题 | 指标 | 能回答 | 不能回答 |
|---|---|---|---|
| 证据找全了吗 | supporting fact EM/F1、Hit@K、MAP@K | 句/段级证据是否命中 | 证据之间的推理是否正确 |
| 多跳推理对吗 | joint EM/F1、accuracy | 答案+证据联合正确 | 哪一步推理错 |
| 长文中间忘了吗 | accuracy by position | 位置偏差 | 为什么会忘 |
| 真能处理 100K 吗 | effective length、∞Bench accuracy | 长度上的性能保持 | 真实业务分布 |
| 长文任务全面吗 | LongBench 分项 | 多任务画像 | 检索/引用质量 |

---

## 8. 指标清单汇总

> 说明：`常见阈值` 一列，能引原文报告值的写原文；没有普适阈值的写"无通用阈值"，并给工程参考（明确标注"经验值"）。不要把工程经验当成论文结论。

| 指标 | 定义 / 机制 | 取值 | 用它的论文 | 常见阈值 / 参考 |
|---|---|---|---|---|
| Recall@k | 相关文档中进入 top-k 的比例：`#(R ∩ top-k) / #R` | 0–1，越高越好 | DPR、BEIR、T2Ranking、RAGChecker | 无通用阈值；RAG 工程常以 ≥0.9 为目标（经验值） |
| Precision@k | top-k 中相关文档比例：`#(R ∩ top-k) / k` | 0–1 | BEIR、T2Ranking | 无通用阈值；候选多时天然低 |
| MRR@k | 第一个相关文档排名倒数的均值：`mean(1/rank_first)` | 0–1 | T2Ranking、MultiHop-RAG | 无通用阈值；单证据任务常用 |
| MAP@k | 每个 query 的 AP@k 再平均；AP 对每个相关位置的 precision 取平均 | 0–1 | MultiHop-RAG、BEIR（MAP@100） | 无通用阈值；多证据任务更合适 |
| Hit@k | top-k 中至少命中一个相关证据的 query 比例；MultiHop-RAG 按证据覆盖定义，需看原文 | 0–1 | MultiHop-RAG | 无通用阈值；定义随论文变化，必须写清 |
| nDCG@k | `DCG@k / IDCG@k`，`DCG=Σ(2^rel-1)/log2(i+1)` | 0–1 | BEIR、ColBERTv2、MTEB、T2Ranking | BEIR 跨数据集约 0.3–0.8，不可跨数据集直接比 |
| Top-k retrieval accuracy | top-k 任一段包含答案字符串即命中 | 0–1 | DPR | 无通用阈值；NQ top-20 约 0.78 |
| Success@k | top-k 至少一个相关段落即成功 | 0–1 | ColBERTv2（LoTTE） | 无通用阈值 |
| EM | 归一化后答案与标准答案完全一致 | 0/1，平均 | FiD、HotpotQA、Self-RAG、MultiHop-RAG | 对别名/格式敏感；多跳 joint 更低 |
| F1（答案） | 答案 token 级 precision/recall 的调和平均 | 0–1 | FiD、HotpotQA、LongBench | 无通用阈值；长答案更稳 |
| ROUGE-L | 最长公共子序列的 precision/recall/F | 0–1 | CRUD-RAG、LongBench、∞Bench | 与人工弱相关；中文政策文本尤甚 |
| BLEU | n-gram precision + brevity penalty | 0–1 | CRUD-RAG | 与人工弱相关；不适合开放生成 |
| BERTScore | 上下文嵌入的贪心匹配相似度 | 0–1 | CRUD-RAG | 测语义相似，不测事实正确 |
| Accuracy（QA） | 精确匹配/包含标准答案的比例 | 0–1 | CRAG、RGB、Self-RAG、MultiHop-RAG、∞Bench | 受判分脚本影响；必须写判分规则 |
| Faithfulness（RAGAS） | 被上下文支持的陈述 / 总陈述 | 0–1 | RAGAS、ARES（同维） | 无通用阈值；0.8–0.9 常被当工程目标（经验值） |
| Answer Relevance（RAGAS） | 由答案反生成问题的平均余弦相似度 | 0–1 | RAGAS、ARES | 无通用阈值；相关≠正确 |
| Context Relevance（RAGAS） | 相关句 / 总句 | 0–1 | RAGAS、ARES | 无通用阈值；短上下文可刷高 |
| Context Precision（RAGAS 2.x） | 逐 k 的 precision 对相关块取平均 | 0–1 | RAGAS 2.x 库 | 需要参考块；不是 2023 论文指标 |
| Context Recall（RAGAS 2.x） | 参考陈述被上下文支持的比例 | 0–1 | RAGAS 2.x 库 | 需要参考陈述；不是 2023 论文指标 |
| TRACe Relevance | 相关 token / 上下文 token | 0–1 | RAGBench | 无通用阈值；token 对齐敏感 |
| TRACe Utilization | 被用 token / 上下文 token | 0–1 | RAGBench | 无通用阈值 |
| TRACe Completeness | 被用相关 token / 相关 token | 0–1 | RAGBench | 无通用阈值 |
| TRACe Adherence | 答案是否完全被上下文支持（布尔/词级） | 0–1 | RAGBench | 长答案易被判 0；看分任务 |
| Claim Recall（RAGChecker） | 参考 claim 被检索覆盖的比例 | 0–1 | RAGChecker | 无通用阈值；诊断用 |
| Context Utilization（RAGChecker） | 检索块覆盖且被响应蕴含的参考 claim / 检索块覆盖的参考 claim | 0–1 | RAGChecker | 越高越好；原文称与整体 F1 强相关 |
| Context Precision（RAGChecker） | 检索块中相关块 / k | 0–1 | RAGChecker | 与 RAGAS 同名不同义，引用时写清出处 |
| Faithfulness（RAGChecker） | 响应 claim 被上下文蕴含 / 响应 claim | 0–1 | RAGChecker | 无通用阈值；claim 抽取敏感 |
| Hallucination（RAGChecker） | 上下文不蕴含的错误 claim / 响应 claim | 0–1，越低越好 | RAGChecker | 与 RAGTruth 的 span F1 不同 |
| Self-knowledge（RAGChecker） | 不靠上下文也正确的 claim / 响应 claim | 0–1，越低越好 | RAGChecker | 判定型任务里未必是坏事 |
| Noise Sensitivity | 被相关/无关噪声块蕴含的错误 claim / 响应 claim | 0–1，越低越好 | RAGChecker | 分 relevant/irrelevant 两列 |
| Citation Recall（ALCE） | 被引用段落完全支持的陈述 / 总陈述 | 0–1 | ALCE、Self-RAG | ELI5 最好约 0.5；不要设 0.9 |
| Citation Precision（ALCE） | 真正支持陈述的引用 / 总引用 | 0–1 | ALCE、Self-RAG | 不惩罚冗余引用；需与 recall 同报 |
| AIS / attributable accuracy | 输出完全被引用证据支持且可解释的比例 | 0–1 | AQA、ARES | 人工金标；AutoAIS 是近似 |
| AutoAIS | NLI 自动判"输出是否被证据支持" | 0–1 | AQA | 与人工系统级 Pearson 0.96；个体仍会错 |
| AttrScore | 三分类（attributable/extrapolatory/contradictory）的 F1 | 0–1 | AttrScore | 每类 F1 + micro-F1（=accuracy）同报 |
| RAGTruth Span F1 | 幻觉片段与人工标注的 span 级 F1 | 0–1 | RAGTruth | QA 58.2、Writing 38.8；任务差异大 |
| RAGTruth Response F1 | 整条响应是否有幻觉的 F1 | 0–1 | RAGTruth | 长响应会被稀释 |
| FActScore | 被支持的原子事实 / 总原子事实 | 0–1 | FActScore | InstructGPT 0.42、ChatGPT 0.58、PerplexityAI 0.71 |
| SelfCheckGPT AUC-PR | 一致性分数判幻觉的 PR 曲线面积 | 0–1 | SelfCheckGPT | 0.5 随机；NonFact 85.63 |
| SelfCheckGPT Pearson | 段落级一致性分数与人工幻觉率的相关 | -1–1 | SelfCheckGPT | 64.71/64.91（百分制） |
| Truthfulness（CRAG） | 逐题 1/0.5/0/-1 的均值 | -1–1 | CRAG | 最好 RAG <0.20；刻度是人为权重 |
| Hallucination rate（CRAG） | incorrect / 总题 | 0–1，越低越好 | CRAG | 与 truthfulness 一起报 |
| Missing rate（CRAG） | missing / 总题 | 0–1 | CRAG | 不是"错误"，但也不是"答对" |
| Rejection rate（RGB） | 正确拒答 / 不可答 | 0–1 | RGB | 最高 45%（英）；固定拒答句限制 |
| Error detection rate（RGB） | 识别出反事实的比例 | 0–1 | RGB | 与 correction 分开 |
| Error correction rate（RGB） | 改正反事实的比例 | 0–1 | RGB | 通常低于 detection |
| Supporting Fact EM/F1 | 句级支撑事实与标注的重合 | 0–1 | HotpotQA、MultiHop-RAG | 句级，不是要件级 |
| Joint EM/F1 | 答案与支撑事实同时正确的联合指标 | 0–1 | HotpotQA | 比单独指标低很多 |
| Effective Length（RULER） | 仍达阈值（Llama2-7B@4K=85.6%）的最大长度 | token 数 | RULER | 阈值人为；看退化曲线 |
| wAvg（RULER） | 13 个任务的加权平均准确率 | 0–1 | RULER | 权重按任务；单点不如曲线 |
| Accuracy by position | 按证据位置分组的准确率 | 0–1 | Lost in the Middle | U 形；中间最差 |
| LCC | 代码编辑的 Levenshtein 相似度 | 0–1 | LongBench | 代码专用 |
| Cohen's κ | 两名标注者超出随机一致的程度 | -1–1 | ALCE、AttrScore、RAGAS | 0.61–0.80 substantial；>0.80 almost perfect（Landis & Koch 惯例） |
| Pearson / Spearman | 自动指标与人工评分的相关 | -1–1 | RAGChecker、ARES、AQA | 系统级 vs 个体级要写清 |

**补充（不属于上述论文，但判定型任务会用）**：

| 指标 | 定义 | 用途 |
|---|---|---|
| 四类混淆矩阵 | 行=金标状态，列=预测状态，4×4 | 看 `unknown` 被误判成 `satisfied` 的比例 |
| Macro-F1 | 四类 F1 的算术平均 | 类别不平衡时比 accuracy 稳 |
| False-satisfied rate | 金标非 `satisfied` 但预测 `satisfied` / 金标非 satisfied | 判定型任务的核心安全指标 |
| Coverage | 有结论（非 unknown）的样本 / 应处理样本 | 与 selective risk 配套 |
| Selective risk | 有结论样本中的错误率 | 固定 coverage 报点 |
| AURC | risk–coverage 曲线下面积 | 汇总，不作主指标 |
| ECE | 预测置信度与准确率的校准误差 | 判断"unknown"置信度是否可信 |
| Brier score | 预测概率与 0/1 结果的均方误差 | 四类概率校准 |
| Citation verifiability | 引用条文存在且 span 能在原文精确匹配的引用 / 总引用 | 机械门禁；可与 LLM 判定解耦 |

---

## 9. 这些指标各自的失效场景

### 9.1 表面重叠指标：ROUGE / BLEU / EM / F1

**机制上为什么坏**：它们奖励 token/片段重叠，不奖励语义等价。政策文本里"应当"与"必须"、"可以"与"原则上可以"只差一个词，法律含义可能相反；反过来，完全改写但正确的答案会被判低分。

**证据**：
- RAGChecker 的 meta-eval 中，ROUGE-L/BLEU 与人工的相关显著低于 claim 级的 **61.93**（原文表；具体表值 `待核`，不在正文里硬写）。
- AQA 中 EM 与人工 AIS 的系统级 Pearson 仅 **0.45**，而 AutoAIS 与人工 AIS 达 **0.96**。
- CRUD-RAG 仍用 ROUGE/BLEU，是因为中文长文生成缺少更好的自动指标；这不代表它们适合判定型任务。

**替代**：判定型任务用**状态混淆矩阵 + 条文级引用 P/R**；生成文本用 claim 级 faithfulness/attribution，而不是 ROUGE。

### 9.2 检索指标：qrels 不完整、chunk 不匹配、cutoff 太短

**机制上为什么坏**：
- qrels 只标注了"参与 pool 的系统"的结果，新系统的相关文档未标注就被当负例 → Recall 低估、nDCG 低估。
- RAG 的检索单位是 **chunk**，qrels 的单位可能是 **document/paragraph**；chunk 切分改变命中。
- nDCG@10/Recall@50 的 cutoff 是人为的；证据在 11–50 名时 nDCG@10 完全看不到。

**证据**：BEIR 的 TREC-COVID 在补标注后分数变化是经典案例；RAGChecker 也把检索指标建立在 claim/chunk 标注上，chunk 切分或标注变化会传导到分数。T2Ranking 的 BM25 Recall@50 仅 0.4918，说明中文段落检索的候选召回本身就难。

**替代**：报告 qrels 来源/标注完整性；用 **Recall@k（k 足够大）+ nDCG@10** 两个口径；chunk 级评测时同时报 document 级命中；对判定型任务定义 **条文级 qrels**，不要用段落 chunk 代替条文。

### 9.3 nDCG：需要分级标注，二元 qrels 用不出优势

**机制上为什么坏**：nDCG 的 gain 依赖 `rel` 等级；多数 BEIR 数据集只有 0/1，nDCG 退化为"排序位置的折损"。如果 gain 用 `2^rel-1` 还是 `rel` 没写清，分数不可复现。qrels 的等级定义（T2Ranking 的 4 级）也需要与业务对齐：Level 2 是"部分相关"还是"可接受"？

**替代**：有分级标注才用 nDCG；否则用 Recall@k/MRR。报告时必须写 gain 公式、等级映射、cutoff。

### 9.4 RAGAS faithfulness：不评引用归属，也不防"矛盾上下文"

**机制上为什么坏**：faithfulness 的输入是"答案 + 上下文"，判定是"答案是否被上下文蕴含"。它不知道答案引用了哪一段，因此：
- 上下文里同时有"旧版条文"和"新版条文"，答案支持旧版 → faithfulness 高，但引用/时效错。
- 答案被某段支持，但那段是**无关段落** → faithfulness 高，context relevance 可能低，但二者都不直接罚"引错"。
- 答案的每个 claim 都被支持，但**结论**（如 satisfied）是规则误用 → faithfulness 仍然高。

**证据**：RAGAS 的三个指标里没有 citation/attribution 项；ALCE 的 citation precision 才测"引用是否支持陈述"。Magesh et al. 2405.20362 的 "misgrounded" 案例（引对段落但法域/时效错）正是 faithfulness 的盲区。

**替代**：citation precision/recall（ALCE/AIS）+ 条文级核验；faithfulness 只作为"文本支持"的诊断项。

### 9.5 Context Relevance / Answer Relevance：容易被"短"和"像"骗

**机制上为什么坏**：
- Context Relevance 是"相关句 / 总句"，只放一句相关句就能拿 1.0，但证据可能不充分；反之，长上下文里大量相关句会被稀释。
- Answer Relevance 是"由答案反生成的问题与原问题的相似度"，答案只要"像在回答"就能高；错误答案也可以很相关。

**替代**：Context Relevance 换成 **证据覆盖率/要件覆盖率**；Answer Relevance 换成 **状态正确性 + 引用支持**。二者只做辅助诊断。

### 9.6 Citation Recall/Precision：不罚冗余、不判权威、不判适用

**机制上为什么坏**：
- Citation Precision 的分母是"引用数"，不要求最小引用集；把相关段落全引上，precision 可能仍高（只要都支持）。
- Citation Recall 只要求"至少一个引用段落完全支持"，多段组合支持/隐含推理不算。
- NLI 对否定、条件、数字、例外条款不稳健；"完全支持"的阈值由模型定。
- 不检查引用段落是否**权威、最新、同法域**。

**证据**：ALCE 的 κ 只有 0.698/0.525，说明人工判 citation 本身就不容易一致；ELI5 最好模型 50% 缺完整支撑。

**替代**：判定型任务用 **条文级 citation P/R + 要件覆盖 + 版本/法域校验**；把"引用存在"（机械可验证）与"引用支持"（NLI/人判）分成两个指标。

### 9.7 FActScore：原子事实拆分与知识源时效

**机制上为什么坏**：FActScore 依赖"原子事实拆分"和"可靠知识源"。拆分过细会把一个法律要件拆成多个事实，分母膨胀；知识源 cutoff 会把"新政策"判成错；"被知识源支持"不等于"被本案证据支持"。

**替代**：法律场景用 **条文级支持 + 要件级覆盖**；FActScore 适合传记/常识长文，不适合政策适用。

### 9.8 SelfCheckGPT：一致性不是事实性

**机制上为什么坏**：模型可以稳定地输出同一个错误（尤其训练数据里的常见误解）；采样温度低时一致性高但错误不变；采样温度高时正确事实也会因措辞不同被判不一致。

**替代**：需要外部证据/知识源；一致性只作为"低资源初筛"，不能单独当幻觉率。

### 9.9 RAGTruth / 幻觉检测：检测 ≠ 修正，span ≠ 法律单元

**机制上为什么坏**：span F1 奖励"定位到词级片段"，但法律错误常是**要件遗漏/条件误用**，不是一个片段；Writing 任务 span F1 仅 38.8，说明长文定位很难；检测器判"无依据"不等于"错误"（可能模型参数知识正确但上下文没写）。

**替代**：判定型任务用 **状态错误分类 + 要件覆盖**；幻觉检测只作为"文本是否越界"的辅助。

### 9.10 多跳 supporting fact：句级证据 ≠ 推理链

**机制上为什么坏**：HotpotQA 的 supporting fact 是句子级，模型可以选对句子但推理错；joint EM 把"答案对 + 句子对"绑在一起，任一错就归零，无法区分"找错证据"与"推理错"。MultiHop-RAG 的 Hit@K 只说明证据在 top-K，不说明顺序/组合。

**替代**：增加 **推理步/要件级** 标注；分开报"证据召回"与"状态正确"；用 error taxonomy 而不是单一 joint 分数。

### 9.11 长上下文指标：合成任务、阈值、污染

**机制上为什么坏**：
- Lost in the Middle 的位置消融是合成的，真实 RAG 的段落相关性与冗余不同。
- LongBench 的"长"约 6.7k 词，不能外推到 100K。
- ∞Bench 的 PassKey/Needle 类任务容易饱和，模型可以靠位置策略而非理解。
- RULER 的 effective length 依赖 85.6% 阈值，换阈值排名会变。

**替代**：报告**退化曲线**而不是单点；用真实业务长文（政策文件、合同）做领域评测；长上下文指标只作为"模型能力上限"，不替代 RAG 检索/引用指标。

### 9.12 LLM judge：自我偏好、位置偏差、成本

**机制上为什么坏**：judge 倾向自己的生成风格、长答案、靠前选项；不同 judge 的绝对分不可比；同一 judge 换 prompt 分数漂移。CRAG 用两个 judge 平均、ARES 用 PPI 校准，都是在补这个洞，但不能完全消除。

**替代**：人工抽检 + κ；固定 judge 版本/prompt/温度；报置信区间；关键结论至少双 judge 或人判。

### 9.13 统计：小样本、单种子、无区间

**机制上为什么坏**：n=50 的 0.90 准确率，95% Wilson 区间约 [0.79, 0.96]；两个系统差 2 个点可能完全在噪声内。RAG 论文常只报单次运行，不给种子/区间。

**替代**：报 `k/n` + Wilson/Clopper-Pearson 区间（见 [`ABSTENTION-CITATION-EVAL.md`](ABSTENTION-CITATION-EVAL.md)）；多种子；配对检验；子组样本量不足时不要下结论。

---

## 10. 如果目标是"带证据的结构化判定"，哪些指标还能用、哪些会误导

### 10.1 任务定义与指标错配

目标输出不是自由文本，而是：

```json
{
  "status": "satisfied | unsatisfied | unknown | not_applicable",
  "citations": [
    {"article": "《XX条例》第12条", "span": "……", "supports": ["condition_1", "condition_2"]}
  ],
  "reason": "……"
}
```

这与开放问答的评测假设有四处根本不同：

1. **输出是离散状态**，不是文本；EM/F1/ROUGE 测不到状态。
2. **`unknown` 是合法输出**，不是"答错"；accuracy 会把"该 unknown 时 unknown"与"该 satisfied 时 satisfied"混在一起。
3. **`not_applicable` 与 `unsatisfied` 是不同类**；标准 QA 没有这个区分，F1 不会惩罚"把不适用判成不满足"。
4. **引用要落到条文/要件**，不是段落；citation recall 的"段落级完全支持"太粗。

### 10.2 还能用的指标（按优先级）

| 优先级 | 指标 | 怎么改造 | 为什么还能用 |
|---|---|---|---|
| P0 | **四类混淆矩阵 + macro-F1** | 4×4，行=金标，列=预测 | 直接测状态；能看每一类被误判成什么 |
| P0 | **False-satisfied rate** | 金标 ∈ {unsatisfied, unknown, not_applicable} 但预测 satisfied / 金标非 satisfied | 判定型任务最危险的错误是"不该满足却满足" |
| P0 | **Citation verifiability** | 引用条文存在 + span 能在原文精确匹配 / 引用数 | 机械可验证，不依赖 LLM；可做到 100% |
| P1 | **条文级 Citation Precision/Recall** | 把 ALCE 的"段落"换成"条文/款/项"；陈述换成"要件" | 测"引的是不是对的条文"，比段落级更贴近 |
| P1 | **AIS / AttrScore 三分类** | 对"状态 + 引用"整体判 attributable/extrapolatory/contradictory | 能区分"引了但不充分"与"引了但相反" |
| P1 | **要件覆盖率** | 已覆盖要件 / 规则要求要件 | 法律适用的核心：条件是否齐全 |
| P2 | **Recall@k（条文级）** | qrels 用条文 ID，不用 chunk | 检索层仍必要；但 k 要够大，且要按要件分组 |
| P2 | **nDCG@10（条文级）** | 有分级相关性才用 | 排序质量；二元 qrels 下退化为 Recall |
| P2 | **Risk–coverage / AURC** | coverage 定义在"应处理样本"；risk=有结论中的错误 | 拒答/unknown 的选择性预测；见既有文档 |
| P3 | **ECE / Brier** | 四类概率校准 | "unknown"置信度是否可信 |
| P3 | **Cohen's κ** | 状态/引用双标注 | 人工一致性；κ<0.6 时先修标注规范 |

### 10.3 会误导的指标（不要当主指标）

| 指标 | 为什么会误导 | 可以怎么用 |
|---|---|---|
| EM / F1 / ROUGE / BLEU | 输出是状态+引用，不是答案文本；表面重叠不测法律适用 | 只用于 `reason` 的可读性辅助，不作主指标 |
| Accuracy（单一） | 类别不平衡时"全判 satisfied"也可能高；掩盖 false-satisfied | 必须与 per-class P/R、混淆矩阵同报 |
| RAGAS faithfulness | 不评引用归属；矛盾条文/旧版条文下仍可能高 | 诊断"文本是否越界"，不判状态 |
| Context Relevance | 句子比例，可被短上下文刷高 | 辅助看检索噪声 |
| Answer Relevance | 相关≠正确；错误判定也可以很相关 | 不作主指标 |
| Citation Recall（段落级） | 不要求最小引用集；不判条文权威/时效 | 改成条文级 + 版本校验 |
| Hallucination rate（单一） | 不支持 ≠ 错误；正确适用也可能"不支持"于某段 | 与状态错误率分开报 |
| MultiHop Hit@K / supporting fact EM | 证据命中不等于推理正确 | 作为检索诊断 |
| Long-context NIAH / effective length | 合成任务，测的是"能不能找到"，不是"适用对不对" | 模型选型参考，不作业务指标 |

### 10.4 建议的判定型指标栈

**主表（每次评测都报）**：

| 层 | 指标 | 分母 | 目标（示例，按项目定） |
|---|---|---|---|
| 状态 | 四类混淆矩阵、macro-F1 | 样本数 | macro-F1 ≥ 0.80（示例） |
| 安全 | False-satisfied rate | 金标非 satisfied | ≤ 2%（示例，带 CI） |
| 引用存在 | Citation verifiability | 引用数 | 100%（机械核验） |
| 引用支持 | 条文级 citation precision / recall | 引用数 / 要件数 | precision ≥ 0.90、recall ≥ 0.85（示例） |
| 要件 | 要件覆盖率 | 规则要件数 | ≥ 0.95（示例） |
| 检索 | 条文级 Recall@k | 金标条文数 | ≥ 0.90（k 按业务定） |
| 拒答 | risk@coverage=0.5/0.7/0.9 | 有结论样本 | 按风险预算定 |
| 校准 | ECE | 样本数 | ≤ 0.05（示例） |
| 人工 | 状态 κ / 引用 κ | 双标注 | ≥ 0.60 才采信自动指标 |

> 这些"目标"是**项目 SLO 示例**，不是论文阈值。论文里没有"判定型任务"的通用阈值；阈值应由风险预算、人工基线、样本量共同决定。

**报告模板（一段话）**：

> 在 n=___ 条应处理样本上，状态分布为 satisfied __ / unsatisfied __ / unknown __ / not_applicable __。四类 macro-F1 = ___；false-satisfied = __/__ = __% [Wilson 95% CI __–__]。引用 __ 条，机械可验证 __/__ = __%；条文级 citation precision = __/__ = __%，recall = __/__ = __%。要件覆盖率 __/__ = __%。检索条文 Recall@10 = __%。risk@coverage=0.7 = __%。状态 κ = ___，引用 κ = ___。

### 10.5 一个具体例子：经济补偿的"满足/不满足/未知/不适用"

规则（简化）：解除劳动合同且符合法定情形 → 应支付经济补偿；若劳动者主动辞职（非被迫）→ 不满足；若工作年限/月工资缺失 → unknown；若不存在劳动关系 → not_applicable。

| 情况 | 金标 | 系统若输出 | 指标怎么记 |
|---|---|---|---|
| 公司违法解除，事实齐全 | satisfied | satisfied + 引用第 46/47 条 | 状态对；citation precision/recall 看条文与要件 |
| 劳动者主动辞职，非被迫 | unsatisfied | satisfied + 引用第 46 条 | **false-satisfied**；citation 可能"支持"条文但适用错 |
| 工作年限缺失 | unknown | satisfied（编造年限） | false-satisfied；幻觉检测可能同时报 unsupported |
| 无劳动关系（实习/劳务） | not_applicable | unsatisfied | 状态错，但不是"不安全"错误；混淆矩阵可见 |
| 事实齐全，但引用旧版条文 | satisfied | satisfied + 旧版 | 状态对；citation verifiability 可能过，**版本校验**不过 |

这个例子说明：**状态正确、引用支持、引用有效**是三个独立维度；任何单一指标都会漏掉其中一种错。

### 10.6 最终结论

1. **检索层**：保留 Recall@k + nDCG@10，但单位改成**条文/款**，qrels 要按要件标注；不要用段落 chunk 的召回冒充条文召回。
2. **端到端**：CRAG/RGB/RAGChecker 的**诊断思路**可用，分数不可直接迁移；RAGChecker 的 claim 级拆分最适合做错误分析，但成本高。
3. **生成质量**：RAGAS/ARES/RAGBench 的 faithfulness/relevance 只能当**文本支持**诊断；判定型任务的主指标是**四类混淆矩阵 + false-satisfied rate**。
4. **归因引用**：ALCE 的 citation P/R 是最接近的现成指标，但要改成**条文级 + 要件级**，并加**版本/法域/权威性**校验；AIS/AttrScore 用来区分"支持/外推/矛盾"。
5. **幻觉**：RAGTruth/FActScore/SelfCheckGPT 测的是"不支持/不一致"，不是"判定错误"；不要用幻觉率代替状态错误率。
6. **多跳/长文**：supporting fact/Hit@K 只证明"证据找到"，不证明"适用正确"；长上下文指标只用于模型选型。
7. **统计**：所有比例写 `k/n` + 区间；所有 LLM judge 写版本/prompt/温度；所有人工指标报 κ。没有区间的"95%"不可信。

---

## 11. 核查状态与待办

**已核实（2026-11-12，arXiv abs/HTML 200）**：DPR、BEIR、ColBERTv2、T2Ranking、MTEB、FiD、RETRO、Self-RAG、CRAG、RGB、RAGChecker、RAGAS、ARES、RAGBench、CRUD-RAG、AQA、ALCE、AttrScore、RAGTruth、FActScore、SelfCheckGPT、HotpotQA、MultiHop-RAG、Lost in the Middle、LongBench、∞Bench、RULER。

**`待核` 清单（不要在正式材料里当确定事实）**：

- RETRO 的 ICML 2022 venue；Self-RAG 的 ICLR 2024 venue；RAGAS 的 EACL 2024 Demo venue；RAGTruth 的 ACL 2024 venue；MultiHop-RAG / RAGBench / CRUD-RAG / AQA 的 venue。
- T2Ranking 的 nDCG gain 公式（原文用 `2^rel-1` 还是 `rel` 需按表确认）。
- ∞Bench Zh.QA 的平均长度（原文表出现 2068.6K，疑为 206.8K 排版错误）。
- RAGChecker 的 ROUGE-L/BLEU 与人工 Pearson 的具体表值（正文只给 claim 级 61.93；表值未取）。
- AttrScore 的 Vicuna 两列具体指标名；GPT-4 "最高约 85%" 的准确出处。
- RAGTruth 的 overall span/response F1（本文只列分任务值，未合成 overall）。
- CRAG 的 human F1（论文正文说人工一致性用 F1，但具体数值在附录/表中，未取）。
- ALCE 的 ASQA/QAMPARI 分数据集数字（本文只列 ELI5 的 50% 与总体 κ）。
- RAGAS 2.x context precision/recall 的官方定义链接：`https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/`、`.../context_recall/`（页面存在，但版本迭代快，引用时写"截至 2026-11 的 2.x 文档"）。

**未纳入但值得后续补**：

- RAGAS-v2：本次检索未发现以 "RAGAS-v2" 为题的独立论文；RAGAS 2.x 是库版本，新增 context precision/recall 等，不能与 2023 论文混为一谈。
- AIS 原始论文（Rashkin et al., 2021）：本文通过 AQA 的定义引用，未单独核原文。
- 中文 C-MTEB / C-Pack：MTEB 覆盖 112 语言，但中文政务领域仍需自建；后续可补 C-MTEB 的 retrieval 子集。
- 法律/政策领域的 RAG 评测（如 LeCaRD、CAIL）：与本文的通用指标不同，后续单独盘点。

---

## 12. 一页纸速查

- **检索**：先问"单位是什么"（段落/条文/要件），再问"k 够不够大"，最后才看 nDCG。
- **端到端**：benchmark 分数是入口，RAGChecker 式拆分才是诊断。
- **生成**：faithfulness 测"有没有瞎编"，不测"引得对不对"；citation P/R 测"引得对不对"，不测"适用对不对"。
- **幻觉**：unsupported ≠ incorrect；检测 ≠ 修正。
- **多跳**：supporting fact 是句级，不是推理链；Hit@K 是检索，不是推理。
- **长文**：NIAH/effective length 是模型能力，不是 RAG 质量。
- **判定型**：主指标 = 四类混淆矩阵 + false-satisfied rate + citation verifiability；其余全部降为诊断项。
- **统计**：`k/n` + 区间；没有区间的比例不要写进结论。
