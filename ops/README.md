# 定时采集（ops）

## 一句话

每天 08:30 扫 8 个上海人社栏目，存快照、和上次比、只抓新增，出一份日报；
**预期落空就报警**，而不是等某个文件缺了才发现。

```bash
bash ops/install.sh      # 安装（写入 ~/Library/LaunchAgents）
bash ops/uninstall.sh    # 卸载
```

手动跑：

```bash
.venv/bin/python -m tools.jobs.collect --dry-run          # 只扫列表，不抓正文
.venv/bin/python -m tools.jobs.collect --only 规范文件     # 只跑一个栏目
.venv/bin/python -m tools.jobs.collect --backfill         # 首次也抓历史全量（慎用）
```

## 为什么是 launchd 而不是 cron

macOS 上 cron 已废弃，且要授予「完全磁盘访问」才能读写用户目录；
launchd 是系统自带、可被 `launchctl` 管理、漏跑会在下次开机补跑。
不引入 Airflow/Celery：一个每天跑 4 分钟的单机任务，加调度框架是给自己找故障面。

## 它到底做了什么

| 步骤 | 说明 |
|---|---|
| 扫栏目 | 自动翻页（读页脚 `totalPage`），1 req/s |
| 存快照 | `data/watch/<栏目>/<时间戳>.json`，是证据，可 diff |
| 比对 | 以**文号**为键，输出 added / revised / removed |
| 抓新增 | 只抓列表里新出现的文号，已处理过的直接跳过 |
| 出日报 | `data/reports/YYYY-MM-DD.md`，一行一个栏目 |
| 记流水 | `data/manifests/collect.jsonl`，一次运行一行 |

## 告警规则（只报「预期落空」）

| 规则 | 为什么 |
|---|---|
| 已验证栏目抓到 0 条 | 栏目改版 / 被 WAF 拦 / UA 失效 |
| 所有栏目都 0 条 | 站点整体不可达 |
| 内容哈希没变但条数变了 | 解析错乱（同一份内容不可能条目数不同） |
| 抓取抛异常 | 单次失败会重试，但必须留痕 |

**不告警**：某个栏目这周没发新文件。那是正常，不是故障——政策是月级低频。
有告警时进程 `exit 1`，launchd 会记录退出码，日报里也会置顶。

## 仍然靠人的三件事

1. **入口确认**：新加一个城市/部门，栏目 URL 要人先看过一次（自动发现只缩小范围）。
2. **结构变更**：列表页改版不会报错，只会「0 条」——告警能发现，但改选择器要人。
3. **内容核验**：「抓到了但抽不出字」是静默失败（扫描件、表格型 docx），
   目前只有 `docs/PIPELINE.md` 的已知问题清单，没有自动指标。

## 日志

```
data/logs/collect.log        # 正常输出
data/logs/collect.err.log    # stderr（含告警）
tail -f data/logs/collect.log
```
