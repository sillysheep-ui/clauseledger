"""定时采集 job：扫栏目 → 存快照 → 和上次比 → 只抓新增/变更 → 出报告 + 告警。

设计约束（每条都有原因，不是风格选择）：
1. **只跑确定性的活**。判断"抓到的东西算不算数"不在这里，这里只负责抓与记。
2. **增量靠 diff，不靠重扫全站**。快照本身是证据，diff 是从证据派生的。
3. **失败要能被人看见**。0 条、0 新增、digest 不变但页数变了，都是告警信号——
   解析器失败是静默的，唯一能发现它的就是这种"预期落空"的检查。
4. **可重入**。同一份文件重跑不产生重复记录（按文号跳过）。

用法：
    python -m tools.jobs.collect                 # 全量：扫所有栏目，抓新增
    python -m tools.jobs.collect --dry-run       # 只扫列表与 diff，不抓正文
    python -m tools.jobs.collect --only 规范文件 --max-pages 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.watch.watch import Item, diff, fetch_column, save

CST = timezone(timedelta(hours=8))
WATCH = Path("data/watch")
REPORTS = Path("data/reports")
LOG = Path("data/manifests/collect.jsonl")
PROCESSED = Path("data/processed")

# 只登记验证过的栏目。`verified=False` 的栏目 0 条不算故障（可能本来就没内容）。
COLUMNS: list[dict] = [
    {"name": "规范文件",
     "url": "https://rsj.sh.gov.cn/tgwgfx_17726/index.html", "verified": True},
    {"name": "其他文件",
     "url": "https://rsj.sh.gov.cn/tgwqt_17736/index.html", "verified": True},
    {"name": "社保",
     "url": "https://rsj.sh.gov.cn/tshbx_17729/index.html", "verified": True},
    {"name": "就业培训",
     "url": "https://rsj.sh.gov.cn/tjypx_17728/index.html", "verified": True},
    {"name": "工资福利",
     "url": "https://rsj.sh.gov.cn/tgzfl_17732/index.html", "verified": True},
    {"name": "劳动关系",
     "url": "https://rsj.sh.gov.cn/tldgx_17731/index.html", "verified": True},
    {"name": "人事人才",
     "url": "https://rsj.sh.gov.cn/trsrc_17730/index.html", "verified": True},
    {"name": "其他",
     "url": "https://rsj.sh.gov.cn/tqt_17733/index.html", "verified": False},
]


def slug_for(url: str) -> str:
    """与 tools.watch.watch.save() 的目录名保持同一算法，否则读不到上次快照。"""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", url.split("//")[-1].lower()).strip("-")


for _c in COLUMNS:
    _c["slug"] = slug_for(_c["url"])


def now() -> datetime:
    return datetime.now(CST)


def snapshots(slug: str) -> list[Path]:
    d = WATCH / slug
    return sorted(d.glob("*.json")) if d.exists() else []


def prev_items(slug: str) -> list[Item] | None:
    files = snapshots(slug)
    if not files:
        return None
    data = json.loads(files[-1].read_text("utf-8"))
    return [Item(**x) for x in data.get("items", [])]


def processed_doc_nos() -> set[str]:
    out: set[str] = set()
    for p in PROCESSED.glob("*.json"):
        try:
            d = json.loads(p.read_text("utf-8"))
            if d.get("doc_no"):
                out.add(d["doc_no"])
        except Exception:
            continue
    return out


def check_alerts(results: list[dict], have_prev: bool) -> list[str]:
    """告警只看"预期落空"，不看单次失败——单次失败会重试，落空不会自己好。

    - 已验证栏目抓到 0 条  → 栏目改版 / 被拦 / UA 失效
    - 所有栏目都 0 条      → 站点整体不可达
    - 有快照但没有新增     → 不是故障，只是这周没发文（不告警，只记录）
    - digest 与上次相同但条数变了 → 不可能，说明解析错乱
    """
    alerts: list[str] = []
    if not results:
        return ["没有任何栏目被扫描"]
    if all(r["items"] == 0 for r in results):
        alerts.append("所有栏目都返回 0 条：站点可能整体不可达或被拦")
        return alerts
    for r in results:
        if r["verified"] and r["items"] == 0:
            alerts.append(f"栏目「{r['name']}」返回 0 条（该栏目已验证过有内容）")
        if r["digest_prev"] and r["digest_prev"] == r["digest"] and r["items"] != r["items_prev"]:
            alerts.append(f"栏目「{r['name']}」内容哈希未变但条数从 {r['items_prev']} 变为 {r['items']}，解析可能错乱")
        if r["error"]:
            alerts.append(f"栏目「{r['name']}」抓取失败：{r['error']}")
    return alerts


def run_column(col: dict, *, do_ingest: bool, max_pages: int | None,
               limit: int, have: set[str], backfill: bool = False) -> dict:
    t0 = time.time()
    r = {"name": col["name"], "slug": col["slug"], "verified": col["verified"], "baseline": False,
         "items": 0, "items_prev": 0, "digest": "", "digest_prev": "",
         "added": 0, "revised": 0, "removed": 0, "ingested": 0, "skipped": 0,
         "new_docs": [], "error": "", "seconds": 0.0}
    try:
        snap = fetch_column(col["url"], max_pages=max_pages)
        prev = prev_items(col["slug"])
        r["items"] = len(snap.items)
        r["digest"] = snap.digest()
        if prev is not None:
            from tools.watch.watch import Snapshot
            r["items_prev"] = len(prev)
            r["digest_prev"] = Snapshot(col["url"], "", 0, prev).digest()
            events = diff(Snapshot(col["url"], "", 0, prev), snap)
            r["added"] = sum(1 for e in events if e["action"] == "added")
            r["revised"] = sum(1 for e in events if e["action"] == "revised")
            r["removed"] = sum(1 for e in events if e["action"] == "removed")
            added_items = [i for i in snap.items
                           if i.doc_no and i.doc_no not in {p.doc_no for p in prev}]
        elif backfill:
            added_items = [i for i in snap.items if i.doc_no]
        else:
            # 首次扫描只建基线：不把历史全量当成"新增"灌进语料
            added_items = []
            r["baseline"] = True
        save(snap, WATCH)

        todo = [i for i in added_items if i.doc_no and i.doc_no not in have][:limit]
        r["skipped"] = len(added_items) - len(todo)
        if do_ingest:
            from tools.ingest.pipeline import ingest
            for it in todo:
                try:
                    ingest(it.url, hint={"doc_no": it.doc_no, "title": it.title,
                                         "issued_at": it.issued_at, "valid_until": it.valid_until})
                    r["ingested"] += 1
                    have.add(it.doc_no)
                    r["new_docs"].append({"doc_no": it.doc_no, "title": it.title[:40]})
                except Exception as e:  # 单条失败不拖垮整批，但必须留下痕迹
                    r["new_docs"].append({"doc_no": it.doc_no, "error": str(e)[:120]})
    except Exception as e:
        r["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc(limit=1)
    r["seconds"] = round(time.time() - t0, 1)
    return r


def write_report(results: list[dict], alerts: list[str], dry: bool) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    ts = now()
    p = REPORTS / f"{ts:%Y-%m-%d}.md"
    lines = [f"# 采集日报 {ts:%Y-%m-%d %H:%M}", "",
             f"模式：{'只扫列表（dry-run）' if dry else '列表 + 抓取新增'}", ""]
    if alerts:
        lines += ["## ⚠️ 告警", ""] + [f"- {a}" for a in alerts] + [""]
    else:
        lines += ["## ✅ 无告警", ""]
    lines += ["| 栏目 | 条数 | 新增 | 变更 | 删除 | 抓取 | 复用 | 耗时 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        tag = "（基线）" if r.get("baseline") else ""
        lines.append(f"| {r['name']}{tag} | {r['items']} | {r['added']} | {r['revised']} | "
                     f"{r['removed']} | {r['ingested']} | {r['skipped']} | {r['seconds']}s |")
    new = [d for r in results for d in r["new_docs"]]
    if new:
        lines += ["", "## 新增文档", ""]
        for d in new[:50]:
            lines.append(f"- {d.get('doc_no')} {d.get('title', '')} {d.get('error', '')}".rstrip())
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def derive() -> str:
    """重算有效期链 + 重建索引。失败不阻断采集：报告里留一行，人能看到。"""
    try:
        from app.retrieval import index as I
        from tools.ingest import validity as V

        docs = V.load()
        res = V.build(docs)
        stats = V.apply(res, docs)
        V.write_derived(res, docs)
        built = I.build()
        return (f"派生完成：延期边 {len(res.edges)} 条，"
                f"被延长 {stats['extended']} 份，索引 {built['docs']} 份 / {built['clauses']} 条")
    except Exception as e:  # pragma: no cover
        return f"派生失败（{type(e).__name__}: {e}）——检索仍用上一版索引"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只扫列表与 diff，不抓正文")
    ap.add_argument("--only", default=None, help="只跑某个栏目名")
    ap.add_argument("--max-pages", type=int, default=None, help="每栏目最多翻几页")
    ap.add_argument("--limit", type=int, default=50, help="每栏目最多抓多少份新文档")
    ap.add_argument("--backfill", action="store_true", help="首次扫描也抓历史全量（慎用）")
    ap.add_argument("--no-derive", action="store_true",
                    help="只采集，不重算有效期/索引（调试用）")
    args = ap.parse_args()

    cols = [c for c in COLUMNS if not args.only or c["name"] == args.only]
    if not cols:
        print(f"没有匹配的栏目：{args.only}", file=sys.stderr)
        return 2

    have = processed_doc_nos()
    results = [run_column(c, do_ingest=not args.dry_run, max_pages=args.max_pages,
                          limit=args.limit, have=have, backfill=args.backfill) for c in cols]
    alerts = check_alerts(results, have_prev=any(snapshots(c["slug"]) for c in cols))
    report = write_report(results, alerts, args.dry_run)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now().isoformat(timespec="seconds"),
                            "dry": args.dry_run, "alerts": alerts,
                            "columns": [{k: v for k, v in r.items() if k != "new_docs"}
                                        for r in results]}, ensure_ascii=False) + "\n")

    total_new = sum(r["ingested"] for r in results)

    # 派生层：有效期链与索引都依赖新抓到的文件。
    # 放在**采集之后**跑，是为了让"新到一份延期通知"当天就反映到检索结果里——
    # 状态不是存下来的字段，是每次从证据重新算出来的结论。
    if not args.dry_run and not args.no_derive:
        print(derive())
    print(f"扫描 {len(results)} 个栏目，抓取 {total_new} 份新文档 → {report}")
    for a in alerts:
        print(f"[告警] {a}", file=sys.stderr)
    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
