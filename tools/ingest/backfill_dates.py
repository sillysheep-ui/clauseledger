"""回填 processed 记录里的日期字段（幂等，可反复跑）。

用法：
    python -m tools.ingest.backfill_dates --dry-run
    python -m tools.ingest.backfill_dates
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter
from pathlib import Path

from tools.ingest.dates import extract

DATA = Path(os.environ.get("CLAUSELEDGER_DATA", "data"))


def atomic_write(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", type=int, default=20, help="报告里抽样几份")
    args = ap.parse_args()

    files = sorted((DATA / "processed").glob("*.json"))
    if not files:
        print("没有 processed 记录")
        return 1

    before = Counter()
    after = Counter()
    conflicts: list[dict] = []
    rows: list[dict] = []
    changed = 0

    for p in files:
        doc = json.loads(p.read_text(encoding="utf-8"))
        before["issued_at"] += bool(doc.get("issued_at"))
        before["effective_to"] += bool(doc.get("effective_to"))

        got = extract(doc)
        fields = got.as_fields()

        after["issued_at"] += bool(fields["issued_at"])
        after["effective_to"] += bool(fields["effective_to"])
        after[f"src:{fields['issued_at_source']}"] += 1
        after[f"to:{fields['effective_to_source']}"] += 1

        if (
            got.signs.date
            and got.published_at
            and got.signs.date != got.published_at
        ):
            conflicts.append(
                {
                    "doc_no": doc.get("doc_no"),
                    "sign": got.signs.date,
                    "list": got.published_at,
                }
            )

        rows.append(
            {
                "file": p.name,
                "doc_no": doc.get("doc_no"),
                "title": (doc.get("title") or "")[:34],
                "sign_raw": got.signs.raw[:50],
                "sign": got.signs.date,
                "list": got.published_at,
                "eff": f"{fields['effective_from']} ~ {fields['effective_to']}",
                "note": fields["date_note"],
            }
        )

        if args.dry_run:
            continue
        doc.update(fields)
        atomic_write(p, json.dumps(doc, ensure_ascii=False, indent=1))
        changed += 1

    n = len(files)
    print(f"{'[dry-run] ' if args.dry_run else ''}扫描 {n} 份，写回 {changed} 份")
    print(f"  issued_at     {before['issued_at']} → {after['issued_at']} ({after['issued_at']/n:.0%})")
    print(f"  effective_to  {before['effective_to']} → {after['effective_to']} ({after['effective_to']/n:.0%})")
    print(f"  落款来源      {dict((k[4:], v) for k, v in after.items() if k.startswith('src:'))}")
    print(f"  有效期来源    {dict((k[3:], v) for k, v in after.items() if k.startswith('to:'))}")
    print(f"  落款与列表日期不一致 {len(conflicts)} 份")

    if not args.dry_run:
        rep = DATA / "reports"
        rep.mkdir(parents=True, exist_ok=True)
        rnd = random.Random(42)
        sample = rnd.sample(rows, min(args.sample, len(rows)))
        lines = [
            "# 日期字段回填报告",
            "",
            f"扫描 {n} 份，写回 {changed} 份。",
            "",
            "| 指标 | 回填前 | 回填后 |",
            "|---|---:|---:|",
            f"| issued_at | {before['issued_at']} | **{after['issued_at']}** ({after['issued_at']/n:.0%}) |",
            f"| effective_to | {before['effective_to']} | **{after['effective_to']}** ({after['effective_to']/n:.0%}) |",
            "",
            "## 落款与列表日期不一致",
            "",
            "| 文号 | 落款 | 列表 |",
            "|---|---|---|",
        ]
        for c in conflicts[:40]:
            lines.append(f"| {c['doc_no']} | {c['sign']} | {c['list']} |")
        lines += [
            "",
            f"## 抽样 {len(sample)} 份（seed=42，供人工核对）",
            "",
            "| 文号 | 落款原文 | 落款 | 列表 | 生效区间 | 备注 |",
            "|---|---|---|---|---|---|",
        ]
        for r in sample:
            lines.append(
                f"| {r['doc_no']} | {r['sign_raw']} | {r['sign']} | {r['list']} | {r['eff']} | {r['note']} |"
            )
        (rep / "dates-backfill.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  报告 → {rep / 'dates-backfill.md'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
