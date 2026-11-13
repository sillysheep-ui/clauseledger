# 存储层：把"没有这份文件"拆成五种情况

> 这一层只回答"我们手上有什么"，不回答"它是什么意思"。
> 解析、判定、效力、去重都不在这里做。

## 一、三个对象

| 对象 | 是什么 | 存在哪 | 主键 |
|---|---|---|---|
| **Object** | 不可变字节 | `data/raw/<sha256前12位>/<原名>` | 内容哈希 |
| **Document** | 一份政策（业务身份） | `data/processed/<文号>.json` | 文号 |
| **Source** | 一次来源（url → 字节） | `data/registry/sources.jsonl` | (doc_no, role) |

为什么不是"一个文件一个 JSON"：**332 份文档没有附件**（正文在网页里），**35 份是多附件**。
按文件建模，前者没有文件、后者文件数不定，主键会跟着内容变。按记录建模，这些差异只是字段。

一条记录的字段里，有五个字段专门用来标记"这字节是怎么来的"：

| 字段 | 取值 | 缺失后果 |
|---|---|---|
| `retrieved_via` | crawl / manual:official / manual:mirror | 分不清是抓的还是人抄的 |
| `doc_no_source` | list / text / manual | 文号从正文误抽时无法追责 |
| `page_kind` | html / pdf | 不知道正文是网页还是附件 |
| `extract` | native / ocr / textutil / unsupported | 不知道这三万字是抄的还是认的 |
| `issued_at_source` | document / gazette / database / unknown | 会把公报出版日当成文日期 |

## 二、五种状态（关键）

查不到一份文件，有五件完全不同的事。混成一个"没有"，就是把"我们没拿到"说成"世界上没有"。

| 状态 | 含义 | 回答时怎么说 |
|---|---|---|
| `ingested` | 已入库，有正文 | 正常引用 |
| `listed_only` | 列表里出现过，正文没取 | "在官方列表里出现过，正文尚未抓取" |
| `fetch_failed` | 试过，没拿到字节 | "我们知道它存在，但抓取失败（HTTP 4xx，试了 N 次）" |
| `parse_failed` | 字节在，抽不出文字 | "原文已取到，但没能抽出文字（扫描件/表格）" |
| `probably_exists` | 编号区间有缺口 | "落在编号区间内，但官方栏目没列出；可能未公开，也可能从未发布" |

**最后一种是推测，必须这么说。** 2017 年实测：栏目里实际只有 23 个编号，最大号却是 42——
所以"缺号"里有相当一部分从来不曾存在。`probably_exists` 不叫 `missing`，就是为了不说这个谎。

产出：`data/registry/docs.jsonl`（一行一份文件）、`data/reports/doc-registry.md`（人看）。
当前：**619 条 = 574 ingested + 45 probably_exists**。

## 三、孤儿字节

盘上有、没有任何记录指向的字节。**不删**（删字节违反只增不减），但必须登记，否则就是不可审计的垃圾。

```
data/registry/orphans.jsonl
```

当前 6 个 / 246,641 B，全是早期抓取残留。

## 四、与检索的接口

`Answer.gap`：当检索判定"证据不足"时，附带一份"已知但没取到"的文件，
让用户看到的是"我们没拿到这份"，而不是"没有这项政策"。

```python
ans = search_result("失业保险金怎么申领")
# ans.kind == "insufficient"
# ans.gap  == {"doc_no": ..., "status": "fetch_failed", "text": "...抓取失败..."}
```

## 五、踩过的坑

**失败日志会把已入库的文件降级。** 第一次跑登记器，574 份里有 59 份被判成 `fetch_failed`——
它们早就成功入库，只是早期一次 DNS 抖动留下了失败记录。

修法一句话：**证据优先于日志**。

```python
if cur and cur["status"] in ("ingested", "parse_failed"):
    continue   # 抓取失败过、但后来成功了，状态仍是 ingested
```

## 六、还没做的

- **版本表**：同文号改版目前是"覆盖"，不是"留痕"。设计在 `docs/` 里，代码没建表。
- **来源去重**：同一份内容两个 URL（官方页 + 转载页）现在按文号覆盖，只留一个来源。
  `sources.jsonl` 已经是 1:N 结构，等真有重复再拆。
