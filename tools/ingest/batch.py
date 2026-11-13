"""全量跑一个栏目的快照：断点续跑、逐条记状态、最后出格式分布与失败率。

用法：python -m tools.ingest.batch [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tools.ingest.fetch import fetch
from tools.ingest.parse import parse_any
from tools.ingest.fetch import FetchError
from tools.ingest.pipeline import ingest

CST = timezone(timedelta(hours=8))
PROCESSED = Path("data/processed")
MANIFESTS = Path("data/manifests")


def latest_snapshot(column: str) -> Path:
    files = sorted(Path("data/watch").glob(f"*{column}*/*.json"))
    if not files:
        raise SystemExit(f"没有找到栏目 {column} 的快照，先跑 tools.watch.watch")
    return files[-1]


def ext_of(url: str | None) -> str | None:
    if not url:
        return None
    base = url.split("?")[0].rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower()[:5] if "." in base else None


def read_version(path: Path) -> str | None:
    try:
        return json.loads(path.read_text("utf-8")).get("parser_version")
    except Exception:
        return None


def is_empty(path: Path) -> bool:
    """0 条款，或附件里有 unsupported / 全零字符 —— 这些是解析器的锅，值得重跑。"""
    try:
        d = json.loads(path.read_text("utf-8"))
    except Exception:
        return True
    atts = d.get("attachments", [])
    if any(a.get("kind") == "doc_unsupported" for a in atts):
        return True
    if atts and all((a.get("chars") or 0) == 0 for a in atts):
        return True
    return d.get("stats", {}).get("clauses", 0) == 0


def slug_of(doc_no: str | None, title: str, url: str) -> str:
    import re
    base = doc_no or title[:24] or url.rsplit("/", 1)[-1]
    return re.sub(r"[^\w\u4e00-\u9fff]+", "-", base)[:60]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--column", default="rsj-sh-gov-cn-tgwgfx-17726")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="全部重跑（换解析器版本后归一化）")
    ap.add_argument("--target-version", default=None,
                    help="只重跑不是这个 parser_version 的记录，完成续跑")
    ap.add_argument("--redo-empty", action="store_true",
                    help="重跑「0 条款」或「附件全是扫描件/.doc」的文档（换了解析器时用）")
    ap.add_argument("--workers", type=int, default=3,
                    help="并发数。每请求自带 1s 间隔，3 并发约 2-3 req/s，对政府站点算克制")
    args = ap.parse_args()

    snap = json.loads(latest_snapshot(args.column).read_text("utf-8"))
    items = snap["items"]
    if args.limit:
        items = items[: args.limit]

    MANIFESTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CST).strftime("%Y%m%dT%H%M%S")
    manifest = MANIFESTS / f"ingest-{args.column}-{stamp}.jsonl"

    done, skipped, failed = 0, 0, 0
    t0 = time.time()
    todo = []
    for it in items:
        name = slug_of(it.get("doc_no"), it.get("title", ""), it["url"])
        rec_path = PROCESSED / f"{name}.json"
        need = False
        if not rec_path.exists():
            need = True
        elif args.force:
            need = True
        elif args.target_version:
            need = read_version(rec_path) != args.target_version
        elif args.redo_empty and is_empty(rec_path):
            need = True
        if not need:
            skipped += 1
            continue
        todo.append((name, it))
    if args.dry_run:
        for name, it in todo:
            print("would ingest", it.get("doc_no"), "->", name)
        return

    def work(name: str, it: dict) -> dict:
        t1 = time.time()
        try:
            rec = ingest(it["url"], slug=name, hint=it)
            atts = [ext_of(a.get("url")) for a in rec.get("attachments", [])]
            return {"doc_no": rec["doc_no"], "ok": True, "secs": round(time.time() - t1, 1),
                    "page_kind": rec["page_kind"], "atts": atts, **rec["stats"]}
        except FetchError as e:            # 抓取失败：把状态码与重试次数一并留下
            return {"doc_no": it.get("doc_no"), "ok": False, "secs": round(time.time() - t1, 1),
                    "error": "FetchError", "http_status": e.status,
                    "attempts": e.attempts, "msg": e.last}
        except Exception as e:
            return {"doc_no": it.get("doc_no"), "ok": False, "secs": round(time.time() - t1, 1),
                    "error": type(e).__name__, "msg": str(e)[:200]}

    with manifest.open("w", encoding="utf-8") as mf, \
            ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(work, n, it): n for n, it in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            done += row["ok"]
            failed += not row["ok"]
            mf.write(json.dumps(row, ensure_ascii=False) + "\n")
            mf.flush()
            if i % 10 == 0 or not row["ok"]:
                print(f"[{i}/{len(todo)}] ok={done} fail={failed} skip={skipped} "
                      f"elapsed={time.time() - t0:.0f}s last={row['doc_no']}", flush=True)

    rows = [json.loads(l) for l in manifest.read_text("utf-8").splitlines() if l.strip()]
    ok = [r for r in rows if r["ok"]]
    print(f"\n完成：新处理 {len(ok)}，失败 {failed}，跳过已存在 {skipped}，"
          f"耗时 {time.time() - t0:.0f}s")
    print("页面类型：", dict(Counter(r["page_kind"] for r in ok)))
    print("附件后缀：", dict(Counter(e for r in ok for e in r["atts"] if e)))
    print("失败类型：", dict(Counter(r.get("error") for r in rows if not r["ok"])))
    if ok:
        print(f"平均块 {sum(r['blocks'] for r in ok) / len(ok):.0f}、"
              f"字 {sum(r['chars'] for r in ok) / len(ok):.0f}、"
              f"条款 {sum(r['clauses'] for r in ok) / len(ok):.0f}")
    print("manifest:", manifest)


if __name__ == "__main__":
    main()
