# 自动化评测怎么搭

一句话：**把评测拆成「数据 / 打分器 / 报告 / 运行器」四件，用套件决定跑多深，用缓存把重复成本压掉，用门禁决定能不能合。**

## 1. 四个部件

| 部件 | 位置 | 职责 | 不做什么 |
|---|---|---|---|
| 数据 | `eval/questions_v3.json`、`eval/questions_missing.json`、`eval/official_answers.json` | 冻结的题、负样本、官方答案 | 不做过滤（过滤在 `tools/evalkit/dataset.py`） |
| 打分器 | `tools/evalkit/graders.py` | 条款级证据召回、AEC、拒答结局 | 不调 LLM 判分 |
| 报告 | `tools/evalkit/report.py` | 一张主表 + 失败归类（只报条数） | 不打印文件级 rank |
| 运行器 | `tools/evalkit/run.py` | 套件选择、缓存、并发、落盘 | 不做阈值判断（门禁在 CI） |

## 2. 四个套件

| 套件 | 内容 | 耗时（首次 / 缓存热） | 跑在哪 |
|---|---|---|---|
| `smoke` | dev 前 10 题 + 负样本（12+18） | ~3.5 min / ~10 s | 每次改检索或装配后、PR |
| `full` | dev / test / holdout 全量官方题 + 负样本 | ~60–90 min / 秒级 | 夜间、发布前 |
| `behavior` | 6 条终态断言（C17–C22，`python -m tools.eval_behavior`） | 秒级 | PR、发布前 |
| `citation` | 50 条人工抽样（`python -m tools.eval_citation --sample 50`） | 人工 30 min | 发布前 |

命令：

```bash
python -m tools.evalkit.run --suite smoke
python -m tools.evalkit.run --suite full --split dev
python -m tools.evalkit.run --suite full --debug-rank   # 只在需要诊断时
```

## 3. 缓存怎么失效

缓存键 = `v2-rr<0|1>-<语料 stats.json 的 sha256 前 12 位>`，值 = 每题的
`rank / aec / outcome / taxonomy`。所以：

- 换语料 → 自动全部重算；
- 换重排开关 → 换一个缓存文件；
- 改打分口径 → 提升键前缀（当前 `v2`）；
- 缓存文件在 `eval/cache/`，**不入库**。

## 4. 门禁阈值

| 指标 | 阈值 | 跌破时 |
|---|---|---|
| AEC 证据覆盖 | ≥ 80% | nighty 失败 → 开 issue |
| 域内无政策拒答 / 域外拒答 | 100% | 直接失败 |
| 误答（无政策却作答） | = 0 | 直接失败 |
| 误拒（可答而拒） | ≤ 5% | 警告 |
| 行为断言 | 全绿 | 直接失败 |

## 5. 触发矩阵

| 时机 | 跑什么 |
|---|---|
| 改完一个检索/装配参数 | `smoke`（缓存热几秒） |
| 提交 PR | `smoke` + `behavior` |
| 每夜 02:00 | `full` 三个 split |
| 发布 / 写 README 数字 | `full` + `behavior` + `citation` 抽样 |

## 6. 还没做的

- **LLM judge 不引入**：主指标全部确定性；语义支撑（CSR）用人工 50 条 + κ。
- 判定 30 条标注未写：写好之前，判定准确率在报告里显示"未测"，不显示假数字。
- 行为套件已抽成 `tools/eval_behavior.py`（6/6 通过，秒级，不调模型）；
- 引用抽样已抽成 `tools/eval_citation.py`（`--sample N` 生成 CSV，`--score` 算 CSR 与 κ）。
