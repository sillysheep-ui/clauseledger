# 部署：本机够不够，什么时候需要服务器

> 一句话：**本机（M5 / 24 GB）跑得下整条链路，峰值约 10 GB；服务器只在"公网可访问"这一个需求上才必要，而那个需求对求职作品几乎不加分。**

---

## 0. 结论先行

| 用途 | 放哪 | 判断 |
|---|---|---|
| 演示（录屏、现场） | **本机** | 够，且更好（模型本地、断网可用、无 API 费） |
| 连续运行证据（"它自己跑了 N 天"） | 本机（launchd） | 够 |
| 公网可访问链接 | 才需要服务器 | 对求职几乎不加分 |

不做真部署的理由不是"太难"，是**收益不匹配**：迁移要半天到两天，备案要 2–3 周，而面试官不会点链接。

---

## 1. 资源预算（实测口径）

| 组件 | 常驻内存 | 磁盘 |
|---|---|---|
| FastAPI + BM25 索引（5,736 条条款 / 74.8 万字） | ~300 MB | 6.7 MB |
| Postgres + pgvector（上服务时） | ~300 MB | ~1 GB |
| bge-m3 向量模型（ollama，可选） | ~2.5 GB | 2.2 GB |
| qwen2.5:7b 生成模型（ollama，可选） | ~6 GB | 4.7 GB |
| Playwright Chromium（仅采集时） | ~600 MB | 602 MB |
| OCR（Apple Vision，系统级） | ~200 MB | 0 |
| **合计峰值** | **~10 GB** | **~9 GB** |

当前语料：`data/raw` 284 MB / 875 文件，`data/processed` 14 MB / 573 份，`data/index` 6.7 MB。
即使语料扩 10 倍，磁盘也在 3 GB 量级——**磁盘从来不是约束**。

---

## 2. 迁移时必须换掉的三处（全是 macOS 专有）

| 现在 | 换成 | 代价 |
|---|---|---|
| `ocrmac`（Apple Vision OCR） | PaddleOCR（中文最好）或 tesseract `chi_sim` | PaddleOCR 依赖约 1 GB；tesseract 中文明显更差 |
| `textutil`（.doc → 文本） | LibreOffice `--headless --convert-to txt` | 装约 700 MB，但稳定 |
| launchd | systemd service + timer | 重写 `ops/` |

**关键后果**：OCR 换了，那 103 份扫描件的字就变了，`page / char_offset` 全部失效——
必须整体重建并升 `PARSER_VERSION`，否则引用偏移对不上，审计链断在"页码"这一环。

---

## 3. 三个真风险（按严重度）

### 3.1 服务器 IP 可能被政府站拦

现在能抓，是因为出口 IP 是北京联通家宽。机房 IP（阿里云/腾讯云）对政府站**可能更差**（机房段可疑度更高），也可能更好。

**本地无法验证**。迁移第一件事必须是在目标机上探测：

```bash
.venv/bin/python -m tools.jobs.collect --dry-run --only 规范文件
```

三种结果对应三种处理：正常 → 继续；412/521 → 上 `browser_retry` 的两次请求逻辑；超时 → 换机房或放弃。

### 3.2 无头浏览器缺中文字体

服务器上没装 CJK 字体时，Playwright 渲染出的页面是方块，正文抽取全废。
装 `fonts-noto-cjk` 即可，但**不装不会报错**，只会安静地少一半字——和解析器那三个静默失败同一类。

### 3.3 域名备案

大陆服务器 + 域名 = ICP 备案（2–3 周 + 材料）。两个绕法：不绑域名直接 `IP:端口`（能用但不好看），或放香港/新加坡（免备案，但访问境内政府站可能慢/被拦）。

---

## 4. 上服务器前必须先修的四处

| 位置 | 问题 | 改法 |
|---|---|---|
| `tools/ingest/pipeline.py:107`、`tools/ingest/manual.py:155`、`app/retrieval/index.py:118` | **写文件不是原子的**。本机没有并发读者，服务器上 FastAPI 会读到写了一半的 JSON | 写临时文件 + `os.replace()` |
| `tools/ingest/ocr.py` | Vision 硬编码 | 抽成 `OCR_BACKEND = vision \| paddle \| tesseract` |
| `tools/ingest/parse.py:160` | `textutil` 硬编码 | 抽成 `DOC_CONVERTER = textutil \| libreoffice` |
| 30 处 `Path("data/...")` | 路径写死 | 统一读 `CLAUSELEDGER_DATA` 环境变量 |

前三条里，**原子写是唯一会真正弄坏数据的**；另外三条只是可移植性。

---

## 5. 迁移步骤（若要迁）

1. **先只迁采集 job**，不迁服务。跑三天看 `data/reports/*.md` 的告警。
2. 三天无告警 → 迁 `data/raw`（284 MB）+ `data/processed`，重建索引，比对条款数是否一致。
3. 再迁 FastAPI + Postgres；`systemd` 起两个单元（`collect.timer`、`api.service`）。
4. 最后才谈域名与备案。

判断"迁成功"的标准不是"服务起来了"，而是：
**同一份语料在本机与服务器上跑同一批评测题，hit@1 差值在置信区间内。**

---

## 6. 不做什么

- **不承诺多用户**：7B 模型在 M5 上约 20–40 token/s，并发 1–2。
- **不做内网穿透**给外部临时访问：省不了多少事，反而引入安全面。
- **不在服务器上重新调参**：参数与用例一起冻结，换环境只许换实现，不许换标准。

---

## 7. 所以现在的立场

本机是**演示环境**，也是**开发环境**；服务器是**可选的可移植性证明**。

真要证明"想清楚了"，把这份文档 + 一个 `docker-compose.yml` 交给面试官，比迁一次更能说明问题：
你知道会坏在哪三处，知道第一件事该测什么，也知道为什么现在不做。
