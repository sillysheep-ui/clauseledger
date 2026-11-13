# 覆盖声明（Coverage Disclosure）

> 检索前先说边界，而不是在答案末尾补一句"仅供参考"。

## 为什么单独成篇

用户问"海北州应届生有补贴吗"，系统有两种回法：

```
A. 未找到相关政策。
B. 海北州未接入——该州门户我们没能自动取到原文。
   这不代表当地没有政策，只代表我们没查到。
```

在用户眼里 A 和 B 都像"没有"，但含义相反：**A 是我查过，B 是我根本没查**。
把 B 说成 A，就是这个项目最想避免的那类失败——不是答错，是**假装答过**。

所以覆盖状态不是日志字段，是**回答的一部分**，且必须出现在检索之前。

## 五种结局，两个必须分开的概念

| 结局 | 含义 | 由谁产生 |
|---|---|---|
| `satisfied` / `unsatisfied` / `not_applicable` | 判定结果 | judge |
| `unknown` | 有覆盖，但证据不足以下结论 | judge |
| `no_policy` | **查过**，确实没有相关规定 | 检索 + 规则层 |
| `not_covered` | **根本没查**：城市或事项未接入 | 覆盖前置检查（不进检索） |

后两者是最容易混的。规矩：

- `not_covered` **不允许起检索**——跑了也只是白跑，还会在审计里留下"查过"的假痕迹。
- `no_policy` 必须带检索范围（"已检索 12 份文件"），否则和 `not_covered` 无法区分。

## 四种覆盖状态

| 状态 | 含义 | 能否检索 |
|---|---|---|
| `covered` | 城市可达 + 该事项已建库 | ✅ |
| `partial` | 城市可达，但该事项还没建库 | ✅（但答案必须标注事项未覆盖） |
| `blocked` | 城市站点拿不到（反爬 / 找不到入口 / 域名碰撞） | ❌ |
| `out_of_scope` | 不在地级行政区收录范围内 | ❌ |

**城市可达 ≠ 事项已建库。** 这两个必须分开说，否则"广州没查到落户政策"会被读成"广州没有落户政策"。

## 三处披露

1. **入口横幅**（进入即见，不用点开）
   `已接入 327/337 个城市（含 4 个直辖市；语料版本 cv-20261111-1），10 个未接入`
2. **输入即时校验**：用户输入城市时先查覆盖，`blocked` 直接拦住，不发请求
3. **答案页脚**：每次回答附 `coverage_snapshot`（覆盖数据 hash + 语料版本）

第 3 条是为了半年后回看——覆盖会变，当时看到的边界必须能复原。

## 文案规范

禁止：

- ❌ "暂无相关数据"（把两种含义混成一句）
- ❌ "该地区政策可能有所不同"（含糊其辞）

必须：

- ✅ `未接入：{detail}。这不代表当地没有相关政策，只代表我们没能自动取到。`
- ✅ `已检索 {n} 份文件，未发现相关规定。`

## 数据与代码

| 文件 | 作用 |
|---|---|
| `data/coverage.json` | 覆盖数据（333 地级 + 4 直辖市 = 337，含 blocked 原因与 caveat） |
| `tools/scan/build_coverage.py` | 由门户扫描结果生成，可重跑 |
| `app/domain/coverage.py` | `CoverageIndex.check(city, matter)` / `.disclosure()` |
| `tests/test_coverage.py` | 5 条断言，其中一条专守"没查 ≠ 查了没有" |

blocked 原因分三类，用户看到的解释必须不同：

| reason | 说人话 |
|---|---|
| `waf_blocked` | 站点可访问，但部署了挑战式防护，自动化拿不到正文 |
| `identity_mismatch` | 域名能打开，但页面与城市对不上（多音字碰撞） |
| `discovery_gap` | 我们没找到官方入口，**不是当地没有公开信息** |

## 评测

- 正向用例：问 `blocked` 城市 → 必须返回 `not_covered`，`judgements` 为空，且 `tool_calls` 中**不得出现检索**
- 反向用例：问 `covered` 城市但确实无政策 → 必须返回 `no_policy`，不得退化成 `not_covered`
- 指标 `mislabel_rate`：两者互相冒充的次数，**必须为 0**（防止"什么都答未覆盖"刷分）

## 当前诚实缺口

- `matters` 全部为空 → 现在**所有可达城市都是 `partial`**，因为条款库还没建。这会在 M1 之后逐城填。
- 10 个 blocked：`discovery_gap` 8、`identity_mismatch` 1、`waf_blocked` 1。

## 探测方式必须记下来：`method` / `last_status`

每条城市记录现在带三个字段，说明**这次是怎么试的、试到哪一步**：

| 字段 | 含义 | 取值 |
|---|---|---|
| `method` | 用什么方式拿到的/试的 | `httpx`（直连）/ `browser`（浏览器两段式） |
| `last_status` | 最后一次看到的 HTTP 状态码 | 200 / 412 / 521 / `null`（连接层失败） |
| `attempts` | 这个城市试过几个入口 | 整数 |

**为什么必须区分**：`httpx` 拿不到 ≠ 拿不到。实测 39 个城市只有浏览器能过（`method=browser`），包括成都、合肥这类 412/521 站点。

**当前口径下的结论**：
- 327 可达：`httpx` 274 + `browser` 39 + `chrome` 14
- 10 blocked：8 个没找到入口、1 个城市名碰撞、1 个真拦

### 一次自我纠错：24 → 10

原来有 24 个 blocked，全部是 `method=httpx` 探出来的。**换真 Chrome 重测后 14 个能打开**，
而且打开的原因跟"防护"没太大关系：

| 原判原因 | 实测真相 | 数量 |
|---|---|---|
| 域名打不开 | **我们域名猜错了**（DNS 根本不解析，如 `linxiahuizu.gov.cn`、`gannanzangzu.gov.cn` 这种按民族全名拼的） | 21 |
| 同名碰撞 | `taizhou.gov.cn` 是**江苏泰州**，浙江台州在 `zjtz.gov.cn` | 1 |
| 网络超时 | 清远、德阳 IP 通但 60 秒没响应；换 `gdqy.gov.cn` 立刻可读 | 2 |

改用正确域名后，14 个城市当场可达（台州 6,799 字、湘西 8,076、防城港 10,102、
大理 293,316、临夏 12,689……），剩下 10 个才是真的难取（韶关、贵港、贺州、德阳、
金昌、武威、甘南、黄南、海南、玉树）。

**教训**：`blocked` 是个垃圾桶标签，它同时装着"对方不让"和"我们自己找错了"。
不把这两种分开，覆盖率就是自欺。

## 失败台账

`tools/ingest/ledger.py` 把多次运行的 `data/manifests/ingest-*.jsonl` 按文件汇总到 `data/registry/attempts.jsonl`：

| 字段 | 含义 |
|---|---|
| `attempts` | 一共试过几次 |
| `first_failed` / `last_tried` / `last_ok` | 首次失败、最后一次尝试、最后一次成功 |
| `last_status` / `last_stage` / `last_error` | 最后失败的状态码、阶段（httpx/browser）、原因 |
| `state` | `ok` / `ok_after_retry` / `never_ok` |

实测：**560 份文件里 59 份失败过后来成功，0 份从未成功**——这就是"失败不等于不可达"的直接证据。

## 第三档：真 Chrome（凭指纹，不凭 IP）

同一个 URL、同一台机器、同一个时刻，三种客户端：

| 客户端 | 成都 `www.chengdu.gov.cn` | 为什么 |
|---|---|---|
| `httpx` | **412**，2,382 字节，0 个汉字 | 不执行 JS，停在挑战第一段 |
| Playwright 自带 Chromium | 挑战脚本循环，**0 字** | 真跑 JS，但指纹仍是自动化 |
| **真 Chrome（CDP）** | **200**，360 KB，正常渲染 | 指纹是真的 |

所以对方拦的**不是 IP，是客户端身份**。`tools/ingest/fetch.py::fetch_smart` 现在是三档：

```
httpx  →  无头 Chromium（两段式）  →  真 Chrome（CDP）
```

只有遇到"在考验客户端"的状态码（403/412/418/429/503/521/522/524）才升级，其余照旧走 httpx。

### 判据不是状态码，是"有没有正文"

踩过的坑：一个 7 KB 的挑战页也能过 `200 字节`，但中文字只有 5 个。所以判据改成：

```python
substantive(html) = len(re.findall(r"[\u4e00-\u9fa5]", html)) >= 200
```

拿到正文之后，即使首包是 412，也记 `status=200`、`via=chrome`——因为我们确实拿到了页面。

### 代价（必须写清楚）

- 必须 `--no-sandbox`：本机进程在外层沙箱里，Chrome 自己的 sandbox 起不来（日志 `sandbox initialization failed`）。
  这只适合**本机受信环境**，不能带到生产。
- 一个城市 3–8 秒，不能全量跑；只对挑战状态码触发。
- 用临时 profile（`/tmp/clauseledger-chrome`），不碰用户正在用的 Chrome。
- 见过一种崩法：上次 CDP 断开后 Chrome 停在 `Browser.setDownloadBehavior ... not supported`，
  所以要 `pkill` 重启再试一次（`fetch_with_real_chrome(retry=1)`）。

证据：`docs/scan/shots/证据-成都-真Chrome.png`（真 Chrome 渲染出的成都市政府首页）。
