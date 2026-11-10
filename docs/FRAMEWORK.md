# FRAMEWORK.md — 工程骨架与基础设施规划

> 配套 `DESIGN.md`。这份回答"用什么跑、分几层、为什么这么分"。
> 目标：**任何一个模块挂掉，进程重启后能接着跑**——这是审计优先在工程上的物理体现。

---

## 1. 设计依据（五条原则）

| # | 原则 | 后果 |
|---|---|---|
| 1 | **状态只在 Postgres** | 进程内存里不存任何业务状态，`docker compose restart` 不丢任何东西 |
| 2 | **所有外部写操作有幂等键** | 重放安全，这是"崩溃恢复不重复提交"的前提 |
| 3 | **单写者** | ingest/评测只由 worker 写，api 只读 → 不需要分布式锁 |
| 4 | **依赖单向** | `domain ← adapters ← runtime ← api`，反向 import 一律 CI 拦截 |
| 5 | **边界可替换** | LLM、检索、解析、观测全部走接口，演示时可整层替换成回放 |

选型理由表（"为什么不用 X"）：

| 选择 | 不选 | 理由 |
|---|---|---|
| Postgres + pgvector | MongoDB / 专用向量库 | 事务、版本、审计、向量一个库解决；5000 条款不需要向量数据库 |
| arq | Celery | 纯 async、代码量 1/5、Redis 已经在栈里 |
| Phoenix（默认） | Langfuse（可选档） | 单容器 512MB；Langfuse v3 要 6 个容器 ~2.5GB，演示时启动太慢 |
| LiteLLM 作为库 | LiteLLM Proxy 容器 | 少一个服务；`Router(fallbacks=...)` 库内就支持降级 |
| uvicorn + 单机 compose | k8s / 多副本 | 单用户系统，多副本只会让"单写者"假设失效 |
| Jinja + HTMX | React / Next.js | 演示页面两个就够，前端构建链会吃掉两天 |

---

## 2. 容器清单

### 2.1 默认档（lite，5 个容器）

| 服务 | 镜像 | 端口 | 卷 | 内存 |
|---|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 127.0.0.1:5432 | `pgdata` | 512M |
| `redis` | `redis:7-alpine` | 127.0.0.1:6379 | `redisdata` | 256M |
| `api` | 自建 `docker/api.Dockerfile` | 127.0.0.1:8000 | `data` | 512M |
| `worker` | 同镜像，`arq app.jobs.worker.WorkerSettings` | — | `data` | 512M |
| `phoenix` | `arizephoenix/phoenix:latest` | 127.0.0.1:6006 | `phoenix` | 512M |

**ollama 跑在宿主机**（127.0.0.1:11434），不放进容器——macOS 上容器拿不到 Metal 加速，跑 14B 会慢到没法演示。

### 2.2 可选档（full，+5 个容器）

`langfuse-web:3000`、`langfuse-worker`、`clickhouse`、`minio`、复用 `postgres/redis`。用 compose `profiles: ["full"]` 开关，默认不启动。

### 2.3 compose 骨架

```yaml
name: policy-agent

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: policy
      POSTGRES_PASSWORD: policy
      POSTGRES_DB: policy
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data", "./migrations:/docker-entrypoint-initdb.d:ro"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U policy -d policy"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    ports: ["127.0.0.1:6379:6379"]
    volumes: ["redisdata:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10

  api:
    build: { context: ., dockerfile: docker/api.Dockerfile }
    env_file: [.env]
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    ports: ["127.0.0.1:8000:8000"]
    volumes: ["data:/data"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 10s
      retries: 5

  worker:
    build: { context: ., dockerfile: docker/api.Dockerfile }
    command: ["arq", "app.jobs.worker.WorkerSettings"]
    env_file: [.env]
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    volumes: ["data:/data"]

  phoenix:
    image: arizephoenix/phoenix:latest
    ports: ["127.0.0.1:6006:6006"]
    volumes: ["phoenix:/data"]

volumes: { pgdata: {}, redisdata: {}, phoenix: {}, data: {} }
```

`.env` 关键项：`DATABASE_URL` `REDIS_URL` `OLLAMA_BASE_URL=http://host.docker.internal:11434` `CLOUD_API_KEY` `LITELLM_FALLBACK_MODEL` `PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces` `APP_PROFILE=lite|demo`

> `demo` profile：`LLM_MODE=cassette`，所有模型调用读录制文件，**断网也能完整演示**。

---

## 3. 目录结构（带依赖规则）

```
policy-agent/
├── docker/{api.Dockerfile, compose.yaml, langfuse.compose.yaml}
├── migrations/                     # 纯 SQL，按 001_xxx.sql 递增
├── data/                           # raw 文档（sha256 命名）+ fixtures
├── app/
│   ├── main.py                     # 只做装配，不写业务
│   ├── settings.py                 # pydantic-settings，唯一配置入口
│   ├── api/                        # routes / deps / schemas（薄）
│   ├── domain/                     # ★ 纯领域：实体、端口(Protocol)、规则判定
│   │   ├── entities.py             # Document / Version / Clause / Judgement
│   │   ├── ports.py                # LlmPort / StorePort / RetrievalPort
│   │   └── rules/                  # applicability DSL + matcher（无 IO）
│   ├── adapters/                   # 端口实现
│   │   ├── pg/  redis/  llm/  http/
│   ├── policy/                     # 政策域应用层
│   │   ├── ingest/                 # discover/fetch/classify/parse/chunk/index
│   │   ├── retrieval/              # hybrid + rrf + 元数据过滤
│   │   └── versioning/             # corpus_version / 生效日期
│   ├── graph/                      # LangGraph
│   │   ├── state.py nodes.py edges.py build.py
│   ├── agents/                     # advisor.py auditor.py
│   ├── jobs/                       # arq worker + tasks(ingest/eval)
│   ├── observability/trace.py
│   └── eval/                       # cases/ run.py report.py
├── tests/
└── docs/{ADR-001.md, eval_report.md}
```

**依赖规则（CI 里用 `import-linter` 卡住）**

- `domain/` **不许** import `adapters/ policy/ graph/ api/`（纯逻辑，可单测）
- `policy/` 只能 import `domain/` 和 `adapters/` 的端口
- `graph/` 可以 import `policy/ agents/ adapters/`
- `api/` 只 import `graph/` 和 `jobs/`，不直接碰数据库

违反这条规则，作品就会退化成"一堆能跑的脚本"——面试官问"你的领域层在哪"会答不上来。

---

## 4. 数据模型（核心表）

```sql
-- 所有业务表第一列都是 tenant_id，为 RLS 预留
CREATE TABLE tenants (id uuid PRIMARY KEY, name text, created_at timestamptz DEFAULT now());

CREATE TABLE documents (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  url text, doc_no text, issuer text, title text,
  category text, city text, created_at timestamptz DEFAULT now()
);

CREATE TABLE document_versions (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL, doc_id uuid REFERENCES documents(id),
  content_hash char(64) NOT NULL,
  effective_from date, effective_to date,
  status text NOT NULL CHECK (status IN ('draft','effective','superseded','expired')),
  raw_path text, parser_version text, fetched_at timestamptz DEFAULT now(),
  UNIQUE (doc_id, content_hash)
);

CREATE TABLE clauses (
  id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  version_id uuid REFERENCES document_versions(id),
  article_no text, paragraph_no int, text text NOT NULL, text_hash char(64),
  applicability jsonb,                -- 规则层输入
  embedding vector(1024),
  indexed boolean DEFAULT false
);
CREATE INDEX ON clauses USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON clauses (tenant_id, city, category);

CREATE TABLE ingest_runs (id uuid PRIMARY KEY, tenant_id uuid, status text,
  corpus_version bigint, started_at timestamptz, finished_at timestamptz);
CREATE TABLE ingest_steps (id bigserial PRIMARY KEY, run_id uuid, url text,
  step text, status text, error_code text, attempt int, duration_ms int);

CREATE TABLE runs (id uuid PRIMARY KEY, tenant_id uuid, thread_id text,
  question text, status text, corpus_version bigint, created_at timestamptz);
CREATE TABLE judgements (id bigserial, run_id uuid, clause_id uuid,
  verdict text CHECK (verdict IN ('satisfied','unsatisfied','unknown')), why text, by text);
CREATE TABLE human_gates (id bigserial, run_id uuid, kind text, payload jsonb,
  decision text, decided_by text, decided_at timestamptz);

CREATE TABLE idempotency_keys (key text PRIMARY KEY, run_id uuid, created_at timestamptz);
CREATE TABLE corpus_versions (version bigint PRIMARY KEY, created_at timestamptz, note text);
```

LangGraph 自己的 checkpoint 表放在独立 schema `checkpoint`（`PostgresSaver.setup()` 管理），不和应用表混。

---

## 5. 进程与 API

| 进程 | 职责 | 并发 |
|---|---|---|
| `uvicorn` (api) | 只读查询 + 发起 run + SSE 推流 + resume | 4 workers |
| `arq` (worker) | ingest、eval、批量导入 | 2 |
| `cron`（可选） | 每周复验来源可达性 | 1 |

端点：

```
POST /v1/ask              # SSE，返回 run_id + 事件流
GET  /v1/runs/{id}        # 状态 + 最终答案 + 引用
GET  /v1/runs/{id}/trace  # span 树（从 runs/steps 读，不依赖观测平台）
POST /v1/runs/{id}/resume # HITL 恢复
POST /v1/ingest           # 入队，返回 run_id
GET  /v1/ingest/{id}      # 进度（按 step 聚合）
POST /v1/eval/run         # 跑金标
GET  /v1/eval/{id}        # 报告 JSON + markdown
GET  /healthz /readyz
```

**trace 落两处**：Phoenix（可视化）+ `steps` 表（可查询、可进 eval）。观测平台挂了不影响回放，这是"trace 是产品不是装饰"的体现。

---

## 6. 冷启动与演示

```bash
make up          # lite 档起 5 个容器
make seed        # 导入 3 份 fixture 政策 + 预切条款
make demo        # APP_PROFILE=demo，LLM 走 cassette，断网可演示
make eval        # 跑 50 条金标，输出 docs/eval_report.md
make down
```

`data/manifests/{corpus_version}.json` 记录：git commit、镜像 digest、corpus_version、模型 id、金标集哈希、运行时间。**演示环境可复现**，和 ERP-AGENT 的 manifest 是同一个思路。

---

## 7. 第一天就做的三件事

1. `docker/compose.yaml` + `migrations/001_init.sql` 起库，`SELECT 1` 通
2. `app/domain/` 建空实体 + 端口 Protocol（先写接口不写实现）
3. `app/policy/ingest/` 跑通"一个 URL → 一份条款 JSON"，命令行调，不进 API

**不做的事（第一周）**：前端、Langfuse、MCP、多模型路由、任何页面美化。

---

## 8. 与 DESIGN.md 的对应

| DESIGN 章节 | 落到哪 |
|---|---|
| 文档生命周期 | `policy/ingest/` + `ingest_steps` 表 |
| 版本管理 | `document_versions` + `corpus_versions` |
| 条款级索引 | `clauses` + HNSW |
| 状态机 | `graph/` + `checkpoint` schema |
| 双 agent | `agents/advisor.py` `auditor.py` |
| 七道闸 | `domain/rules/` + `graph/nodes.py` |
| 六级缓存 | `adapters/redis/` + key 里带 `corpus_version` |
| 消融实验 | `eval/run.py --variant baseline|full` |
