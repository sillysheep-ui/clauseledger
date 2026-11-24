# Agent 运行时（AgentScope）

## 是什么

`app/agent/runtime.py` 用一个 AgentScope ReAct 循环替换固定的 11 步流程：
**模型决定下一步调哪个工具**，工具本身仍是我们自己的内核（`app/tools/service.py`）。

```
问题 → 模型选工具 → 工具返回带引用的证据 → 模型决定继续检索 / 判定 / 作答 → 答案（逐句 [E#]）
```

## 为什么这么接

- 框架只做编排：检索、装配、判定、效力、覆盖全是既有代码，一行没重写。
- 工具返回的是 `assemble()` 之后的证据（文号/路径/页码/所在节 + 原文），不是裸 top-5；
  pipeline 和 agent 看到的是同一份证据，对照才成立。
- 模型只能选工具，不能改判定：`judge_luohu` 的结论原样转述，写操作不在工具面里。

## 工具面（三个，全只读）

只放"需要模型临场决定"的能力；必须发生的阶段不做成工具。

| 工具 | 作用 | 对应原来的 |
|---|---|---|
| `search_policy` | 检索 + 装配，返回**少而整**的证据（默认 ≤30 条、≤6,000 字，每条完整条款） | `retrieve` + `assemble` |
| `check_coverage` | 城市/事项覆盖状态（covered / partial / blocked / out_of_scope） | `coverage` |
| `read_clause` | 只有证据标了 `truncated` 时才用，把某一条读全 | 无（新增，按需） |

**不在工具面里的**：判定（`judge`）、逐句核验（`verify`）、门控（`gate_*`）、写操作（`draft_plan`）。
它们是 run 的固定阶段——模型无权决定这些事发不发生。调用方给了 profile，运行时就在
循环**之前**用代码算好判定，作为【系统判定】段落交给模型解释，模型只转述不重算。

证据规则：**按条数与总字数控制，不按每条截断**。极少数超过 1,200 字的条款会标
`truncated=true` 并给出原文长度，模型再用 `read_clause` 补读——绝不静默截半句。

## 现状

- `available()`：装了 `agentscope` 且有 `DEEPSEEK_API_KEY` 才为真；否则调用方回退 pipeline。
- 默认运行时**仍是 pipeline**。切默认的前提是把门控/恢复/事件接到 agent 路径（下一步），
  不是选型问题。
- 20 题对照（同一份证据）：pipeline 金标进上下文 15/20，agent 13/20；agent 多 2 次误拒，
  原因是它把问句改写后触发了 `no_direct_clause`。
- 冷启动：首次 `evidence()` 约 24s（索引 + bge 载入，进程内只付一次），热调用 0.14–0.81s。

## 怎么跑

```bash
# 主环境没装 agentscope 时，用 spike 的隔离 venv 叠加（顺序不能反，否则撞 conda 的旧 mcp）
PYTHONPATH="$PWD/spikes/agentscope/.venv/lib/python3.13/site-packages:$PWD/.venv/lib/python3.13/site-packages:$PWD" \
  .venv/bin/python -c "from app.agent import runtime; print(runtime.run('失业保险金能领多久')['answer'])"
```


## 证据给多少：按预算，不按条数

工业界的数字都按 **chunk**（几百 token）算：

- Anthropic Contextual Retrieval：检索 top-150 → rerank → **top-20** 进 prompt；5/10/20 三个量里 20 最好（[原文](https://www.anthropic.com/news/contextual-retrieval)）。
- Azure AI Search：hybrid top-50~100 → semantic 50→**10** → cross-encoder 20→**5** → LLM 10→**3**（[原文](https://learn.microsoft.com/en-us/azure/search/semantic-ranking)）。
- Lost in the Middle（[arXiv 2307.03172](https://arxiv.org/abs/2307.03172)）：信息落在 prompt 中间会掉点，所以"多给"必须配排序。

我们的单位是**条款**，中位 75 字，不是几百 token 的 chunk。8 条约等于 3 个 chunk，偏少；
按 6,000 字给约等于 20–30 条，才对得上工业界的量级。参数：`CLAUSELEDGER_ASM_ITEMS`（默认 30）、
`CLAUSELEDGER_ASM_BUDGET`（默认 6,000 字）。超 1,200 字的单条才截断并标 `truncated`。

实测（70 道可核对题，L3 判据，rerank 默认开）：2,000 字 50.0%、4,000 字 60.0%、
6,000 字 62.9%、全部装配 65.7%；6,000 字比全给少约 14 条，覆盖只差 2.8pp
（详见 `docs/EVAL-SPEC.md` §6.8）。
