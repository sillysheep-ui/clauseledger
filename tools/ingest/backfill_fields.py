"""回填 agency / published_at（原子写），并出覆盖率报告。

    python -m tools.ingest.backfill_fields
    python -m tools.ingest.backfill_fields --dry   # 只看会变成什么
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from datetime import date
from pathlib import Path

from tools.ingest.dates import sign_date
from tools.ingest.fields import agency_of, published_at_of

PROCESSED = Path("data/processed")
REPORT = Path("data/reports/fields-backfill.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    rows = []
    for p in sorted(PROCESSED.glob("*.json")):
        d = json.loads(p.read_text("utf-8"))
        blocks = d.get("blocks") or []
        agency, a_src = agency_of(d.get("title") or "", blocks, d.get("doc_no") or "")
        published, p_src = published_at_of(blocks, d.get("meta_hint") or d, d.get("page_meta") or {})

        # 成文日期不可能在未来：91 号那份的 issued_at 被列表页"有效期至"列污染成 2031
        iss = d.get("issued_at") or ""
        if iss[:10] and iss[:10] > date.today().isoformat():
            got = sign_date(blocks)
            d["issued_at"] = got.date
            d["issued_at_source"] = got.source if got.date else "unknown"
            d["issued_at_note"] = f"原值 {iss} 在未来，已重取"

        before = (d.get("agency"), d.get("published_at"))
        d["agency"], d["agency_source"] = agency, a_src
        d["published_at"], d["published_at_source"] = published, p_src
        rows.append({"doc_no": d.get("doc_no"), "before": before,
                     "after": (agency, published), "src": (a_src, p_src)})
        if not args.dry:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
            os.replace(tmp, p)

    n = len(rows)
    a_ok = sum(1 for r in rows if r["after"][0])
    p_ok = sum(1 for r in rows if r["after"][1])
    a_src = collections.Counter(r["src"][0] for r in rows)
    p_src = collections.Counter(r["src"][1] for r in rows)

    lines = ["# 字段回填：发文机关 / 印发日期", "",
             f"共 {n} 份。", "",
             "| 字段 | 回填后覆盖 | 来源分布 |", "|---|---|---|",
             f"| `agency` | **{a_ok}/{n}（{a_ok/n:.0%}）** | {dict(a_src)} |",
             f"| `published_at` | **{p_ok}/{n}（{p_ok/n:.0%}）** | {dict(p_src)} |",
             "", "## 抽检（前 12 条）", "",
             "| 文号 | 机关 | 印发 | 来源 |", "|---|---|---|---|"]
    for r in rows[:12]:
        lines.append(f"| {r['doc_no']} | {r['after'][0] or '—'} | {r['after'][1] or '—'} | {r['src'][0]}/{r['src'][1]} |")
    Report = "\n".join(lines) + "\n"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(Report, "utf-8")
    print(f"agency {a_ok}/{n} ({a_ok/n:.0%})  published_at {p_ok}/{n} ({p_ok/n:.0%})")
    print(dict(a_src), dict(p_src))
    print(f"→ {REPORT}" + ("（dry run，未写回）" if args.dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
