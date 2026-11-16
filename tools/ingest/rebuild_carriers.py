"""按"一份文件只解析一个主副本"重建语料，输出到独立目录，便于前后对比。

用法：
    python -m tools.ingest.rebuild_carriers --out data/processed.carriers --limit 5
    python -m tools.ingest.rebuild_carriers --out data/processed.carriers
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from tools.ingest.carriers import rebuild

IN = Path("data/processed")
OUT = Path("data/processed.carriers")
MANI = Path("data/manifests")


def run(out: Path, limit: int | None, only: list[str], dry: bool) -> dict:
    files = sorted(IN.glob("*.json"))
    if only:
        files = [f for f in files if any(o in f.stem for o in only)]
    if limit:
        files = files[:limit]
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in files:
        rec = json.loads(f.read_text("utf-8"))
        before = dict(rec.get("stats") or {})
        new = rebuild(rec)
        after = dict(new.get("stats") or {})
        choice = new.get("carrier_choice") or {}
        row = {
            "file": f.name, "doc_no": rec.get("doc_no"),
            "before_clauses": before.get("clauses", 0), "after_clauses": after.get("clauses", 0),
            "before_chars": before.get("chars", 0), "after_chars": after.get("chars", 0),
            "carrier": choice.get("role"), "kind": choice.get("kind"),
            "reason": choice.get("reason"),
            "carriers": len(new.get("carriers") or []),
        }
        rows.append(row)
        if not dry:
            (out / f.name).write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
    return {"rows": rows, "files": len(files)}


def report(res: dict) -> str:
    rows = res["rows"]
    b = sum(r["before_clauses"] for r in rows)
    a = sum(r["after_clauses"] for r in rows)
    bc = sum(r["before_chars"] for r in rows)
    ac = sum(r["after_chars"] for r in rows)
    kinds = Counter((r["carrier"], r["kind"]) for r in rows)
    lines = [
        f"# 载体重建：{res['files']} 份",
        "",
        f"- 条款：{b} → **{a}**（{(a - b) / b * 100:+.1f}%）",
        f"- 字数：{bc:,} → **{ac:,}**（{(ac - bc) / max(bc, 1) * 100:+.1f}%）",
        f"- 主副本类型：{dict(kinds)}",
        "",
        "| 文件 | 条款 前→后 | 主副本 | 理由 |",
        "|---|---|---|---|",
    ]
    for r in rows[:40]:
        lines.append(f"| {r['doc_no']} | {r['before_clauses']}→{r['after_clauses']} "
                     f"| {r['carrier']}/{r['kind']} | {r['reason']} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    res = run(out, args.limit, args.only, args.dry)
    text = report(res)
    MANI.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (MANI / f"carriers-{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in res["rows"]), "utf-8")
    print(text)


if __name__ == "__main__":
    main()
