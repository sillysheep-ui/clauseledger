"""从落盘原件重解析全库：换解析器版本后跑这个，不重抓。

    用法：
        python -m tools.ingest.reparse_all --dry-run
        python -m tools.ingest.reparse_all --workers 4

为什么不用 batch.py --force：那条路会重新发 HTTP 请求。政府站点随时改版，
重抓等于把"证据"换成"今天的证据"，解析器改动的收益就没法跟前一份比了。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.ingest.parse import PARSER_VERSION
from tools.ingest.pipeline import reparse

OUT = Path("data/processed")
LOG = Path("data/manifests")


def one(path: Path) -> dict:
    rec = json.loads(path.read_text("utf-8"))
    before = rec.get("stats", {})
    try:
        new = reparse(rec)
    except Exception as e:  # 单份失败不能拖垮全库
        return {"file": path.name, "ok": False, "error": f"{type(e).__name__}: {e}"}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)   # 原子写：中途崩溃不会留下半截 JSON
    after = new["stats"]
    return {"file": path.name, "ok": True, "before": before, "after": after,
            "version": new["parser_version"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写回")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stale-only", action="store_true", help="只重解析版本不是当前版本的文件")
    a = ap.parse_args()

    files = sorted(OUT.glob("*.json"))
    if a.stale_only:
        files = [f for f in files
                 if json.loads(f.read_text("utf-8")).get("parser_version") != PARSER_VERSION]
    if a.limit:
        files = files[: a.limit]
    print(f"目标 {len(files)} 份 → parser {PARSER_VERSION}")

    if a.dry_run:
        from tools.ingest.pipeline import reparse as _rp
        for f in files[:6]:
            rec = json.loads(f.read_text("utf-8"))
            new = _rp(rec)
            b, nb = rec["stats"], new["stats"]
            print(f"  {f.name[:40]:42s} 块 {b['blocks']:4d}→{nb['blocks']:4d} "
                  f"字 {b['chars']:6d}→{nb['chars']:6d} 条款 {b['clauses']:3d}→{nb['clauses']:3d}")
        return

    LOG.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = LOG / f"reparse-{stamp}.jsonl"
    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex, log.open("w", encoding="utf-8") as fh:
        for i, r in enumerate(ex.map(one, files), 1):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            ok += r["ok"]
            fail += not r["ok"]
            if i % 50 == 0 or i == len(files):
                print(f"[{i}/{len(files)}] ok={ok} fail={fail} {time.time()-t0:.0f}s", flush=True)
            if not r["ok"]:
                print("  !", r["file"], r["error"][:100], flush=True)
    print(f"完成 ok={ok} fail={fail} 用时 {time.time()-t0:.0f}s → {log}")


if __name__ == "__main__":
    main()
