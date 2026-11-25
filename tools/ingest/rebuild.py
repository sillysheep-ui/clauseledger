"""按当前解析器版本离线重建全部记录（不发 HTTP）。

为什么不用 batch.py：batch 是按 URL 重抓，页面改版后拿到的就不是同一份证据了。
这里从 `data/raw/<sha12>/` 的落盘原件重解析，输入字节不变，只有解析结果变。

    python -m tools.ingest.rebuild --dry-run
    python -m tools.ingest.rebuild
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from tools.ingest.carriers import rebuild
from tools.ingest.parse import PARSER_VERSION
from tools.ingest.pipeline import reparse

DATA = Path("data/processed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="同版本也重建")
    ap.add_argument("--only", default=None, help="只重建含该子串的文件名")
    a = ap.parse_args()

    todo = []
    for f in sorted(DATA.glob("*.json")):
        if a.only and a.only not in f.name:
            continue
        rec = json.loads(f.read_text("utf-8"))
        if not a.force and rec.get("parser_version") == PARSER_VERSION:
            continue
        todo.append((f, rec))
    print(f"待重建 {len(todo)} 份（目标版本 {PARSER_VERSION}）")
    if a.dry_run or not todo:
        return

    t0 = time.time()
    ok = fail = 0
    tables = 0
    for i, (f, rec) in enumerate(todo, 1):
        doc = rec.get("doc_no") or f.stem
        try:
            new = rebuild(rec) if rec.get("carrier_choice") else reparse(rec)
        except Exception as e:  # 单份失败不影响整批
            print(f"  ✗ {doc}: {type(e).__name__}: {e}")
            fail += 1
            continue
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, f)
        ok += 1
        tables += len(new.get("tables") or [])
        if i % 50 == 0:
            print(f"  [{i}/{len(todo)}] ok={ok} fail={fail} tables={tables} "
                  f"{time.time() - t0:.0f}s", flush=True)
    print(f"完成：重建 {ok}，失败 {fail}，表格 {tables}，用时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
