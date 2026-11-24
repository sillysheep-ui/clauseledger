# 约束清单：哪些事"必须永远成立"

> 这一页只回答一个问题：**我们声称的那些规矩，运行时到底有没有被强制执行？**
>
> 它和 `docs/EVAL-SPEC.md` 不重复。那边测的是"能多好"（召回多少、覆盖多少，有分母、有区间）；
> 这里列的是"不许错"（写错一次就是事故），只有两种结局：成立，或者 bug。

## 0. 为什么单独一页

两类东西不能用同一种方法量：

| | 质量 | 约束 |
|---|---|---|
| 例子 | 金标进没进上下文（71.4%） | 没接入的城市不许写待办 |
| 判法 | 对一批题算比例 + Wilson 区间 | 真跑一次，查数据库/事件/HTTP |
| 结论 | "还行，但还能更好" | "是 0 行，还是 6 行" |
| 谁看 | 决定下一步优化什么 | 决定能不能上线 |

把约束混进比例里，就会出现"误答率 2%"这种说法——它听起来像可以接受的误差，实际上
每一次都意味着有人被写进了不该写的待办。**约束没有百分比。**

## 1. 状态图例

| 图例 | 含义 |
|---|---|
| ✅ | 有自动化测试，且是绿的（单测或评测） |
| 🟡 | 只有单元测试 / 离线报表，**没有进系统级套件** |
| 🔵 | 只有比例指标，没有二值断言（见 §3） |
| ⬜ | 目前靠人 |

## 2. 清单

### A. 语料与证据

| # | 约束（不可协商的那句话） | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C1 | 原始字节按内容哈希落盘，永不覆盖 | `tools/ingest/fetch.py` → `data/raw/<sha12>/` | `tests/test_fetch.py` | ✅ |
| C2 | 条款文本必须等于原文区间：`text == 全文[start:end]` | `tools/audit/segmentation.py`（不变量 I0） | `python -m tools.audit.segmentation`，当前 0 违例 | 🔵 |
| C3 | 没抽到字的文件必须标记出来，不许当成"本来就没字" | `tools/ingest/parse.py`（`extract` / `ocr_conf`） | 采集报告按 `extract` 分桶（native/ocr/textutil/unsupported） | 🟡 |
| C4 | 扫描件必须中文识别，低置信度整块丢弃 | `tools/ingest/ocr.py`（`zh-Hans`、`OCR_MIN_CONF=0.5`） | 无自动化，人工抽页核对 | ⬜ |

### B. 效力（文件什么时候有效）

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C5 | 效力是时间的函数：状态由**区间**算出来，不是字段 | `app/domain/validity.py:105 status_for` | `tests/test_validity_intervals.py` | ✅ |
| C6 | 没写到期日 = `unknown`，不等于长期有效；`unknown` 不是警告 | 同上 + `app/answer/cards.py:102` | 同上 | ✅ |
| C7 | 延期理由不得穿越：只引当天之前已生效的通知 | `tools/ingest/validity.py::periods()` 的 `by_on` 过滤 | `test_validity_intervals.py`（理由不穿越用例） | ✅ |

### C. 覆盖（查过 / 没查过）

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C8 | 没接入就不许检索，且不能留下"查过"的痕迹（`n_queries=0`） | `app/runtime/steps.py:53-60`、`app/retrieval/search.py:602` | `tests/test_search_evidence.py` | ✅ |
| C9 | "没接入" ≠ "没有政策"，两句话必须不同 | `app/domain/coverage.py::disclosure()` | `tests/test_coverage.py` | ✅ |

### D. 检索

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C10 | 每条证据必须带可回溯定位（文号 + 页码/段号 + 载体） | `app/retrieval/records.py::citation` | `tests/test_cards.py` | ✅ |
| C11 | 同族旧版不得顶掉"当天有效"的版本 | `app/retrieval/search.py:724 _prefer_effective` | 只有比例（同族错版 0–3 条） | 🔵 |

### E. 判定

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C12 | 非上海 / 非落户不得进入判定器 | `app/runtime/steps.py:76` | `tests/test_runtime.py` | ✅ |
| C13 | 四态：`unknown` ≠ 0 分；未知项按组内最大计入上限 | `app/judgement/luohu.py` | `tests/test_luohu.py`（71 分差 1、上限 89） | ✅ |
| C14 | 每条规则必须带页码；规则文件必须写明依据文号 | `rules/sh/luohu-2026.yaml` | `tests/test_luohu.py` | ✅ |

### F. 生成

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C15 | 每句必须带 `[E#]`；引用必须存在；数字必须能在证据里找到，否则**丢句** | `app/answer/verify.py:38` | `tests/test_draft.py`、`tests/test_answer_verify.py` | ✅ |
| C16 | 生成不得改判定与标题 | `app/runtime/steps.py::_attach_draft` | `tests/test_draft.py` | ✅ |

### G. 门控与副作用

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C17 | 有副作用之前必须有人批准 | `app/runtime/steps.py:216-232` | `tests/test_runtime.py`、`tests/test_api.py` | 🟡 |
| C18 | 停住之前卡必须先放进 state（否则人是在盲批） | `app/runtime/steps.py:102-126` | `tests/test_api.py::test_gate_shows_items_before_approval` | 🟡 |
| C19 | 判定不确定必须停 | 同上 | `tests/test_runtime.py` | 🟡 |
| C20 | 写操作按条幂等；重复批准返回 409 | `app/runtime/db.py:131`（`INSERT OR IGNORE`） | `tests/test_recovery.py`、`tests/test_api.py` | 🟡 |

### H. 恢复与观测

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C21 | 状态与事件在库里，进程随时可死 | `app/runtime/db.py`（SQLite WAL） | `tests/test_store_sqlite.py`、`tests/test_recovery.py` | 🟡 |
| C22 | 恢复只重入被中断的 step，已完成的不重跑 | `app/runtime/engine.py:67-76` | `tests/test_recovery.py` | 🟡 |
| C23 | 每个 run 一棵 span 树，每步一个 span | `app/runtime/tracing.py` | `tests/test_tracing.py` | ✅ |

### I. 评测自身的约束（元）

| # | 约束 | 强制点 | 怎么验证 | 状态 |
|---|---|---|---|---|
| C24 | 金标绑文号，不绑内部 id；解析不到标 `STALE`，不算通过也不算失败 | 评测脚本 | `tests/test_metrics.py`、`eval/CHANGELOG.md` | 🟡 |
| C25 | 调参只看 dev；test/holdout 跑一次就封存 | `eval/FROZEN.json` + `docs/CONVENTIONS.md` | 流程（靠人守） | ⬜ |

## 3. 只能报比例的三件事（不要假装是 0/1）

| 约束 | 为什么不能二值 | 现在的口径 |
|---|---|---|
| C2 文本与区间一致 | 语料重建后可能出现新的偏移；只能每次全量体检报违例数 | 全库 0 违例（2026-11-24 体检） |
| C11 旧版不顶现行 | "哪一版算正确"随 `as_of` 变，属于排序质量 | 同族错版 0–3 条 / 105 |
| C15 每句话都有出处 | 运行时能强制"没出处就丢"，但"丢完还剩多少真话"只能给比例 | 官方事实覆盖 42.1%（16/38） |

数字归一化（"二十四个月" vs 24 个月）属于 C15 的已知缺陷：会误杀正确句，
修法是先归一化再比对，不是把阈值调松。

## 4. 三条规矩

1. **新增一条约束，必须同时写"怎么验证"**；写不出来就进 ⬜ 清单，不要写成"设计上保证了"。
2. **一条约束只留一个强制点**，fail closed（宁可拒答，不要放过）。两个地方各判一次，早晚会不一致。
3. **这一页是清单，不是一次性文档**：状态变了就改，红了就写在上面。

## 5. 下一步（未做）

把 🟡 的 C17–C22 升成 ✅ 的办法就是 P0 行为套件：真跑系统、查数据库/事件/HTTP，
六条用例覆盖"拒答不写待办 / 批准恰好 3 条 / 不确定必停 / 非上海不判定 / 拒绝不写 / 崩溃不重复"。
它不进 `docs/EVAL-SPEC.md` 的任何分母，是独立的一张表。
