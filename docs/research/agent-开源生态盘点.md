# GitHub「Agent 应用类」项目盘点：哪些场景做烂了，哪里还有缝

> 数据采集时间 2026-11-11，star 数为 GitHub API 实时值。筛选口径：有真实业务闭环（不是纯框架/纯 awesome list），覆盖 RAG、工具调用、多 Agent、HITL、评测中的至少两项。

## 一、15 个代表性项目

| # | Repo | Star | 业务场景 | 技术栈 | 评测 | 一句话点评 |
|---|------|------|----------|--------|------|-----------|
| 1 | [Tencent/WeKnora](https://github.com/Tencent/WeKnora) | 37.1k | 企业知识库 + 文档理解 | Go + Vue3 + 多向量库 + MCP | ✅ 有（evaluation topic，Langfuse trace） | RAG 平台天花板，个人别正面刚 |
| 2 | [dataelement/bisheng](https://github.com/dataelement/bisheng) | 12.2k | 企业 Agent 平台（金融落地多） | Python FastAPI + React + RAG + 工作流 | 部分（评测在企业版） | 国内 2B 交付最成熟的平台型开源，全但重 |
| 3 | [PySpur-Dev/pyspur](https://github.com/PySpur-Dev/pyspur) | 5.8k | 可视化 Agent 工作流编排 | TypeScript + React Flow + FastAPI | 部分 | 把 agent 当 DAG 调试，产品感强，可参考交互 |
| 4 | [heymrun/heym](https://github.com/heymrun/heym) | 1.5k | 通用 Agent 运行时/编排 | Python，自托管，含 observability | ✅ 有（observe + evaluate + 人工接管） | 与目标画像最像的基建，读架构而非抄功能 |
| 5 | [Ricky-7-Yan/intelligent-audit-system](https://github.com/Ricky-7-Yan/intelligent-audit-system) | 1.2k | 审计 / 合规检查 | Python FastAPI + LangChain + Neo4j + MySQL | ⚠️ 自称 benchmark，未见数据集 | **最危险的对标**：description 是英文营销话术，README 仍是中文 AutoAudit + 满屏 ✅ |
| 6 | [souvikmajumder26/Multi-Agent-Medical-Assistant](https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant) | 1.0k | 医疗分诊 / 诊断辅助 | Python + LangChain + 多 Agent + CV | ❌ 无 | 演示级医疗 Agent，2025-05 停更，靠选题拿 star |
| 7 | [Shy2593666979/AgentChat](https://github.com/Shy2593666979/AgentChat) | 934 | 通用 Agent 交流平台（中文） | FastAPI + LangChain + Milvus + ES + MCP + HITL | ❌ 无 | 中文社区技术要素最全的个人项目，有 HITL/Memory/Skill，无评测 |
| 8 | [tgoai/tgo](https://github.com/tgoai/tgo) | 658 | 智能客服（多渠道） | TypeScript + RAG + 人工协同 | ❌ 无 | 客服赛道头部，仅 658 star：大厂不屑、个人做烂 |
| 9 | [GoogleCloudPlatform/cymbal-air-toolbox-demo](https://github.com/GoogleCloudPlatform/cymbal-air-toolbox-demo) | 351 | 航司客服 demo | LangGraph + BigQuery + RAG | ❌ 无（codelab） | 官方教学 demo，骨架干净，只适合抄结构 |
| 10 | [matthewprice/JobFinderOS](https://github.com/matthewprice/JobFinderOS) | 190 | 求职 / 招聘撮合 | Claude Code skills + Markdown 编排 | ❌ 无 | 求职者侧工作流，无后端无状态机，提示词工程包装成系统 |
| 11 | [tomasonjo-labs/legal-tech-chat](https://github.com/tomasonjo-labs/legal-tech-chat) | 163 | 合同审查（CUAD） | LangChain + LangGraph + Neo4j GraphRAG | ❌ 无 | **法律赛道第一只是一个博客 demo**，空白的直接证据 |
| 12 | [GeminiLight/gen-mentor](https://github.com/GeminiLight/gen-mentor) | 136 | 自适应学习 / 教育 | Python + 多 Agent + TS 前端 | ✅ 论文级离线评测 | WWW'25 Oral，学术 > 工程，代码是实验脚本 |
| 13 | [qifan777/data-agent-tutorial](https://github.com/qifan777/data-agent-tutorial) | 117 | Text2SQL 数据分析 | Kotlin + Spring AI + StateGraph | ❌ 无 | 教程非产品，但 StateGraph + 双重 RAG + HITL 的分层设计值得抄 |
| 14 | [siqiwang0712-commits/financial-risk-agent](https://github.com/siqiwang0712-commits/financial-risk-agent) | 76 | 财务风险 / 决策授权 | Python 3.11+，CI + 证据链校验 | 部分（CI + evidence 校验） | "预测/授权"分离的设计成熟，财务已有严肃玩家 |
| 15 | [google/adk-python](https://github.com/google/adk-python) | 22.2k | Agent 开发框架（基建） | Python，内置 eval 模块 | ✅ 有（adk eval） | 框架层已被大厂占满，`adk eval` 数据集组织可借鉴 |

**同赛道更大体量、不作为"应用"计入**：dify 160.9k、ragflow 94.0k、FastGPT 30.0k、MaxKB 23.1k、QAnything 14.2k、langfuse 37.3k（评测/可观测基建）。

## 二、场景聚类与拥挤度

| 场景 | 拥挤度 | 头部 star | 判断依据 |
|------|--------|-----------|----------|
| 通用 Agent 平台 / 工作流编排 | **极高** | 160.9k | dify/bisheng/pyspur/heym 四层通吃，个人做平台=自杀 |
| 企业知识库 / RAG 问答 | **极高** | 94.0k | ragflow/WeKnora/FastGPT/MaxKB 全万星，且都转向 agentic retrieval |
| 编码 / 代码审查 | **极高** | 49.8k | 49.8k 头部 + Claude Code skills 生态，已被大厂和插件淹没 |
| 智能客服 | **高** | 658 | 项目多但头部仅 658：人人都能做，没人做深 |
| 数据分析 / Text2SQL | **中高** | 117 | 以教程为主，缺生产级项目；NL2SQL 本身红海 |
| 金融风控 / 投研 | **中** | 283 | FinRisk 76 星已属严肃项目，拼数据与合规 |
| 文档审查 / 审计合规 | **中** | 1.2k | AuditPilot 单点 1.2k 但质量存疑，其余 <200，看似挤其实虚 |
| 医疗 | **中低** | 1.0k | 头部是 demo，其余 <300，多为论文复现 |
| 采购 / 招投标 | **低中** | 445 | BidMaster-Pro 445 星占住中文标书，其余 <150 |
| 招聘 / HR（HR 侧） | **低** | 190（求职者侧） | HR 侧普遍 <10 star，但课程作业烂大街 |
| 教育 /  tutoring | **低** | 136 | 只有论文项目，无产品级，变现难 |
| 法律 / 合同 | **低** | 163 | 头部是博客 demo，无状态机/HITL/评测 |
| 保险理赔 / 核保 | **低** | 43 | 仅微软/AWS 官方 sample（43/36/26） |
| AIOps / 故障应急 | **低** | 69 | 全站 total 186，头部 69，多为安全 skills |

## 三、三个真空隙（需求真实 + 项目少 + 能做深）

**1. 合同 / 条款审查（法务、采购）— 最推荐**
证据：`legal-tech-chat` 163 star 是赛道第一，且只是 TDS 博客配套 demo；`contract-analyst` 6、`PEAT-LLM4LCR` 3、`AskMyLawyer` 5 star。需求是企业法务/采购刚需。**关键优势：CUAD 有 500 份合同、41 类条款人工标注**，可直接算条款抽取 P/R，天然满足评测要求；叠加"抽取→风险定级→人工审批→归档"的可恢复状态机，故事完整。

**2. AIOps 故障应急 / 变更审批 — 技术分最高**
证据：`incident response agent` 全站 total 186，头部 `kodeblue` 69 star，其余 21/12/8/5，且多为 Claude Code skills 而非系统。on-call 降噪、runbook 执行需求极强，天然是长事务：告警→诊断→修复方案→人工批准→执行→回滚，每步都要 checkpoint 和 trace。短板是无公开评测集，需自建故障注入，反成亮点。

**3. 保险理赔材料审核 — 商业价值最高、门槛也最高**
证据：`insurance claims agent` 仅微软 `claims-processing-hack` 36、AWS sample 26、社区 25/43，全是 hack/sample，无产品。业务为材料齐全性检查 + 条款匹配 + 金额核算 + 人工复核，HITL 是监管硬要求。风险：无真实保单数据，需合成理赔包，且要懂核保规则，对应届生业务理解要求高。

> 备选但不建议：招聘筛选。HR 侧确实只有个位数 star，但"简历筛选"是 AI 课程作业默认题，面试官一眼看穿。

## 四、三个"看起来很好"的反例特征

1. **描述与 README 语言/内容不一致，功能全是 ✅**
   AuditPilot：GitHub description 是英文 "governed tools, evaluation harnesses, human review"，README 却是中文《AutoAudit 智能审计决策系统》，功能列表 12 个 ✅ 却没有任何测试目录、没有 eval 数据集、没有可复现的指标。**识别法**：对比 description 与 README 首屏是否"话术 > 实现"，再看有无 `tests/`、`evals/`、`docker-compose.yml`。

2. **论文/教程型伪装成产品**
   `gen-mentor`（WWW'25 Oral，star 136）代码是实验脚本；`data-agent-tutorial`（117 star）本质是 Kotlin 教程；`JobFinderOS`（190 star）只是 Claude Code 的 Markdown skills。共同特征：**没有服务端状态、没有数据库迁移、没有部署脚本**，README 里全是"架构图 + 效果截图"。识别法：看有没有 `alembic/`、`prisma/`、`k8s/`、`Dockerfile`，以及 `pushed_at` 是否停在半年以前。

3. **star 高但复现成本被隐藏**
   依赖闭源模型 key、需要 8GB+ 内存 + MySQL + Neo4j + 向量库四件套、README 只给 Windows 一键脚本、无 `.env.example`。识别法：**5 分钟复现测试**——clone 后只允许执行 README 前 3 条命令；跑不起来就是 demo。另一个信号是 issue 区"how to run"类问题占比高。

## 五、给作品选的结论

"企业级 Agent 平台 + 可恢复状态机 + trace + HITL + 评测"这套词，**已经被人用作 repo description 了**（AuditPilot，1.2k star）。再做一个通用平台没有辨识度。

可行路径：**选一个垂直场景（合同审查最优）+ 把 20% 精力放在业务、80% 放在三个硬点上**——(a) 基于 checkpoint 的中断恢复（进程 kill 后能续跑，有 demo 视频）；(b) 全链路 trace（每次工具调用可回放到 span 级）；(c) 可真跑的评测集（CUAD 子集 + 自建 50 条人工标注，给出 P/R/F1 对比 baseline）。这三点正是那个 1.2k star 项目拿不出的，也是唯一能证明"不是又一个 RAG demo"的证据。
