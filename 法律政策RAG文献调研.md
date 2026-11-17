# 法律/政策领域端到端 RAG：公开证据、数字与失败模式

**结论先行**：法条型问答的端到端准确率公开值集中在 **40–70%**（商用 42–64%，通用 RAG 66%，专用系统 83–92%）；条款级检索 R@1 普遍极低（0.3–31%），但 R@10 可达 77%。据此，我们的 **hit@1 55.4% 不算低**；真正偏低的是**“引用能支撑”20%**（同类 citation recall 为 39–44%，法律长上下文 12–38%）。

## 一、典型架构与各层组件

- **解析**：保留法条层级，并把定义、交叉引用、父级条款挂到每个候选条上（STARA、LawInForce）。这一层比换 embedding 更值钱。
- **切分**：条款/段落级 ID（KAG 用 `articleID#paraCode#idInPara`）；政务场景常用 250 字 / 50 重叠 + m3e-base（软件学报）。
- **检索**：BM25 仍是强基线（bLLeQA 荷兰语 R@100 48.5）；升级路径是 hybrid + 元数据（辖区、生效日期）+ reranker（MAP 15.5→22.9），但 reranker 不稳定（LegalBench-RAG 中 Cohere 反而更差）。
- **判定**：让 LLM 逐条回答“该条是否支持本问”，比相似度阈值可靠（STARA）。
- **生成**：受控流程/确定性算子（SAT-Graph），而非一次 retrieve-then-generate。
- **引用**：NLI 判 support（ALCE）→ 把 query + 全检索上下文纳入的原则驱动评测（CiteEval，与人工 Pearson 0.731，AutoAIS 类仅 0.42）。
- **弃权**：单独用 recall/precision 评测（AbstentionBench）。

## 二、来源清单（15 条）

1. [STARA/LaborBench](https://ar5iv.labs.arxiv.org/html/2603.03300)｜Stanford 在 50 州失业保险法上做 1,647 道二分类题的法定调查基准。架构：解析保留层级→补定义/交叉引用→正则预筛→LLM 逐条判相关性→输出法条引注。数字：通用 RAG 66%（F1 0.67）、Westlaw AI 58%（0.64）、Lexis+ AI 64%（0.41，recall 仅 0.29）、STARA 83%（0.81），修正人工漏标后 92%；Lexis+ 同题重跑一致率仅 50%。借鉴：结构感知检索 + LLM 相关性判定 + 直接法条引注，比商用平台高 19–25 点。

2. [LawInForce-eCFR](https://aclanthology.org/2026.aacl-long.42/)｜清华，版本化法条（eCFR）上的时效性检索与判定。架构：authority（谁有权）/validity（是否有效）/support（是否支撑）三要素检索 + 日期过滤 + answer-flipping 对照集（805 修订对、3,220 问）。数字：R@1 BM25 0.31%、加日期过滤 2.80%、citation-hybrid 9.16%（R@10 77.17%）、AuthorityFirst 30.90%；端到端 48.6–53.8%，Qwen2.5-32B 配 gold 证据 63.33%。借鉴：时效性必须是独立检索维度，并造“修订前后答案相反”的测试集。

3. [bLLeQA](https://aclanthology.org/2026.knowfm-1.4/)｜比利时法律（法/荷双语）RAG 全链路 zero-shot 评测，25,982 条 articles。架构：BM25/dense/混合检索 + reranker + 引用抽取 + 拒答 + 生成。数字：BM25 R@100 48.5、mE5 60.6、BGE reranker 把 MAP 15.5→22.9；仅 71% 的 case 能检索全 gold；最佳 correctness 4.2/5，最好情况仍有约 20% 输出有缺陷；拒答 F1 最高 56。借鉴：80% 量级的系统也要接受 20% 缺陷率，拒答要单列指标。

4. [LegalBench-RAG](https://arxiv.org/abs/2408.10343)｜法律 RAG 检索基准（4 数据集，chunk 级 P@k/R@k）。数字：P@1 最高仅 14.38%（PrivacyQA），最难 MAUD 2.65%；R@64 28.3–84.2%。借鉴：条款级 P@1 个位数是常态，评价要看 R@k 与下游，而非 P@1。

5. [ALCE](https://arxiv.org/abs/2305.14627)｜首个自动引用评测基准（ASQA/QAMPARI/ELI5）。架构：NLI 判定“引用集合是否支撑该句”，分 citation recall/precision。数字：ASQA 上 ChatGPT-16K recall 39.1 / precision 73.4，GPT-4 44.4 / 73.0；ELI5 上仅 12.8–18.3。借鉴：把“引用能支撑”做成 claim 级 citation recall，而不是“引用存在”。

6. [L-CiteEval](https://aclanthology.org/2025.acl-long.263/)｜长上下文保真度评测（11 任务、8K–48K）。数字：单文档 QA 的 citation recall：GPT-4o 38.12、Claude-3.5 37.79、开源最好 GLM-4-9B 28.66、Llama-3.1-70B 26.89。借鉴：“答对”与“引用对”是两件事，必须分开报。

7. [CiteEval](https://aclanthology.org/2025.acl-long.1574/)｜原则驱动的细粒度引用评测（CiteBench 人工标注）。数字：与人工相关性 Pearson 0.731（陈述级），AutoAIS 0.42、AttriScore 0.46。借鉴：评“引用支撑”要把 query 和全部检索上下文一起给评审；二元 NLI 会误罚部分支持的引用。

8. [Hallucination-Free?](https://arxiv.org/abs/2405.20362)｜Stanford 评测 Lexis+ AI、Westlaw AI-AR、Ask Practical Law AI。数字：幻觉率 17–33%；Westlaw AI-AR 准确率仅 42%（另一类法律检索任务，与 LaborBench 不可混用）；incomplete（拒答或未 grounded）分别 18%/25%/62%。借鉴：商用标杆也只有 42%，“答得长”≠“答得对”。

9. [KAG](https://arxiv.org/abs/2409.13731)｜蚂蚁/浙大，KG+向量互索引 + 逻辑形式求解器。数字：电子政务 11,000 文档、492 样本上答案 P/R 从 66.5/52.6 提升到 91.6/71.8；医保政策查询 77.2%。借鉴：chunk id 带条款结构 + 多跳求解，多条款组合题收益最大。

10. [AffairQA](https://raw.githubusercontent.com/OpenSPG/KAG/master/kag/open_benchmark/AffairQA/README_cn.md)｜浙大政务问答数据集，KG 检索事实→生成，评 EM/F1。数字：仓库给出完整复现流程，但 README 未标规模与分数（可获取、需查数据文件）。借鉴：可作中文政务对照，别指望现成 SOTA 数字。

11. [SAT-Graph RAG](https://arxiv.org/abs/2510.06002)｜把法律材料建成结构感知的时序知识图谱。架构：概率隔离（不确定性只留意图理解/语义锚定/叙述合成）+ 确定性图算子（时点检索、语境重建、溯源、影响分析）+ Reason-Act-Observe。数字：作者明确声明是架构规范、无实验数字。借鉴：把时效/适用性判定从 LLM 概率里拿出来，做成可审计的图操作。

12. [AbstentionBench](https://arxiv.org/abs/2506.09038)｜Meta，20 数据集/约 35k 不可答题/6 类场景。数字：推理微调平均使弃权能力下降 24%；模型规模几乎无影响；提示词可缓解但不根治。借鉴：“未知”态必须单独评测和训练，否则越推理越爱硬答。

13. [QualBench](https://aclanthology.org/2025.emnlp-main.303/)｜中文多领域本地化基准，17,000+ 题、6 领域、24 项中国职业资格考试。数字：平均准确率 53.98%；Qwen2.5 超过 GPT-4o。借鉴：中文垂直领域裸模型约 54%，RAG 的增益空间就在这条基线之上。

14. [软件学报·可信政务问答](https://www.jos.org.cn/html/7435.htm)｜华南理工，政务问答原型（政务 LLM + 意图分析 + 向量知识库）。数字：1,901 篇文档 + 10,503 问答对（ScienceDB 10.57760/sciencedb.24847，部分问答由 ChatGPT-3.5 生成）；250 字/50 重叠、m3e-base；BERTScore 68.60，Qwen-72B 67.27，ChatGLM2-6B 63.82；只报 ROUGE/BLEU/BERTScore，不报准确率。借鉴：中文政务系统普遍缺“判定正确率”，我们要自建。

15. [LexEval](https://proceedings.neurips.cc/paper_files/paper/2024/file/2cb40fc022ca7bdc1a9a78b793661284-Paper-Datasets_and_Benchmarks_Track.pdf)｜中文法律知识基准，14,150 题/23 任务。数字：零样本下 GPT-4 平均 52.4、Qwen-14B 48.6、ChatGPT 39.6（Discrimination/Generation/Ethic 三层），结论是“LLM 仍无法有效解决中国法系法律问题”。借鉴：中文法律裸模型水平低，领域微调/RAG 是必需项而非加分项。

## 三、失败模式与比例

- **检索不到**：LegalBench-RAG P@1 2.6–14.4%；bLLeQA 29% 的 case 检索不全；LawInForce 无引注查询 R@1 为 0%。
- **条款对但答错（规则适用）**：LawInForce 给 gold 证据时 91.1% 的错误仍是 rule-application，AuthorityFirst 下 83.0%；典型是把“不超过/不得超过”当成下界。
- **时效错**：STARA 中“历史条款仍留在法典”导致部分 reasoning error；LawInForce 不做日期过滤时 R@1 仅 0.31%。
- **引用不支撑**：ALCE recall 39–44%（ASQA）、12.8–18.3%（ELI5）；商用 incomplete 18–62%。
- **输出不稳定**：Lexis+ 同题重跑一致率仅 50%（同组题 Westlaw 100%）。
- **该拒答却硬答**：拒答 F1 最高 56；推理微调使弃权下降 24%。

## 四、我们的 55% 处于什么位置

**可比性先说清**：55.4% 是“92 题、5,983 条款、单 gold”的 hit@1，属检索指标；公开数字要么是条款级 R@k（LawInForce 0.3–31%、bLLeQA R@100 48–61%、LegalBench-RAG P@1 2.6–14.4%），要么是端到端准确率（42–83%）。数据集、候选池、gold 定义均不同，不能直接比。

**判断**：55% 落在公开检索区间内，属“正常偏好”。我们的 hit@3 78.3% 与 LawInForce 中 citation-hybrid 的 R@10 77.2% 数值接近，说明 gold 基本已在候选集里，瓶颈在**排序与判定**，不在召回（该文 R@1 仅 9.16%）。注意 LawInForce 用的是构造的 answer-flipping 难题，0.31% 不能当我们下限。

**真正偏低的是 20% 的“引用能支撑”**：ALCE 生成端 39–44%，L-CiteEval 单文档 38%，法律商用工具 grounded 准确率 42%。20% 明显低于这些，而“引用可定位 100%”几乎没有信息量——引用存在是最低门槛。

**要判断是否偏低，应补三个对照**：
1. 在同一 92 题上跑 dense / hybrid / hybrid+rerank 三条基线，统一报 clause-level R@1/3/10；
2. 报端到端四态判定准确率（目前缺失），对照 LaborBench 的 66%（通用 RAG）与 83%（专用系统）；
3. 抽 50–100 条按 ALCE 式 citation recall/precision 重标“引用能支撑结论”，对照 39–44%。
另加一组负样本：50 条应判“未知/不适用”的题，量 abstention recall/precision（对照 bLLeQA 最佳拒答 F1≈56）。

## 五、Top 3 值得抄的机制

1. **STARA 的结构扩展检索**：解析时保留层级，给每条候选法条自动挂定义、交叉引用、父条款，再让 LLM 逐条判“是否支持本问”，输出直接引注。解决我们的问题：命中单条但缺定义/例外 → 引用支撑率低。落地：在 5,983 条上建 cross-ref 图 + LLM rerank，用 citation recall 而非 hit@1 验收。
2. **LawInForce 的三要素 + 时间维度**：把“主题相关”拆成 authority/validity/support，检索时带生效日期过滤，并用 answer-flipping 对测时效。解决我们的问题：四态里的“不适用”与法条过期。落地：给每条款建生效/失效区间，构造翻转测试集；SAT-Graph 的“概率隔离 + 时点检索算子”可作为可审计实现方式。
3. **引用与弃权的双指标评测**（ALCE/CiteEval + AbstentionBench）：claim 级 citation recall/precision + abstention recall/precision，把“答对/引用对/该拒答”分开报。解决我们的问题：20% 支撑率不可优化、“未知”态无度量。落地：先人工标 100 条 claim-citation 对，再对“未知”单列混淆矩阵。
