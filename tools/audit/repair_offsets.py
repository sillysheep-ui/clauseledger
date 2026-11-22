"""修复"条款文本与偏移不一致"的文档：从落盘原件重解析（不发 HTTP）。

体检 I0 会发现 text != 全文[start:end]。成因是历史上某些文档的 blocks 与
clauses 来自不同解析器版本（改了解析器但只重建了一半），或者是 _cut_tail
改了文本却没同步 end。这类不一致不会报错，只会让引用切出错误位置，
所以按顺序重建整份文档（blocks + clauses 一起）是唯一稳妥的修法。

    python -m tools.audit.repair_offsets --dry-run
    python -m tools.audit.repair_offsets
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.ingest.carriers import rebuild
from tools.ingest.pipeline import reparse

DATA = Path("data/processed")


def full_text(rec: dict) -> str:
    return "\n".join((b.get("text") or "") for b in rec.get("blocks") or [])


def bad_clauses(rec: dict) -> list[str]:
    full = full_text(rec)
    return [c["path"] for c in rec.get("clauses") or []
            if full[c["start"]:c["end"]] != c["text"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    targets = []
    for f in sorted(DATA.glob("*.json")):
        rec = json.loads(f.read_text("utf-8"))
        bad = bad_clauses(rec)
        if bad:
            targets.append((f, rec.get("doc_no") or f.stem, len(bad)))

    print(f"偏移不一致的文档 {len(targets)} 份｜条款 {sum(n for _, _, n in targets)} 条")
    for f, doc, n in targets[:10]:
        print(f"  {doc} {n} 条")
    if a.dry_run or not targets:
        return

    fixed = failed = 0
    for f, doc, _ in targets:
        rec = json.loads(f.read_text("utf-8"))
        try:
            new = rebuild(rec) if rec.get("carrier_choice") else reparse(rec)
        except Exception as e:  # 单份失败不影响其它
            print(f"  ✗ {doc}: {type(e).__name__}: {e}")
            failed += 1
            continue
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, f)
        left = bad_clauses(new)
        if left:
            print(f"  △ {doc} 重建后仍有 {len(left)} 条不一致")
            failed += 1
        else:
            fixed += 1

    print(f"已修复 {fixed} 份｜仍不一致 {failed} 份")


if __name__ == "__main__":
    main()
