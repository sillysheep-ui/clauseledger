"""对比"拼接式"旧语料与"载体重建"新语料的结构指标。

只看结构（条款数、重复、页码覆盖、零条款），不看检索——检索要重建索引后另跑。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


def _load(d: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(d.glob("*.json")):
        rec = json.loads(p.read_text("utf-8"))
        out[p.name] = rec
    return out


def _stats(recs: dict[str, dict]) -> dict:
    clauses = [c for r in recs.values() for c in (r.get("clauses") or [])]
    by_page = sum(1 for c in clauses if c.get("page"))
    dup = Counter()
    for r in recs.values():
        cnt = Counter((c.get("path"), re.sub(r"\s+", "", c.get("text") or ""))
                      for c in (r.get("clauses") or []))
        dup.update({k: v - 1 for k, v in cnt.items() if v > 1})
    return {
        "docs": len(recs),
        "clauses": len(clauses),
        "chars": sum(r.get("stats", {}).get("chars", 0) for r in recs.values()),
        "zero_clause_docs": sum(1 for r in recs.values()
                                if not (r.get("clauses") or [])),
        "dup_clauses": sum(dup.values()),
        "page_ratio": by_page / len(clauses) if clauses else 0.0,
        "carrier": Counter((r.get("carrier_choice") or {}).get("role") for r in recs.values()),
        "max_clause": max((len(c.get("text") or "") for c in clauses), default=0),
    }


def main() -> None:
    old = _load(Path("data/processed"))
    new = _load(Path("data/processed.carriers"))
    a, b = _stats(old), _stats(new)
    rows = [
        ("文件数", a["docs"], b["docs"]),
        ("条款数", a["clauses"], b["clauses"]),
        ("总字数", a["chars"], b["chars"]),
        ("同文重复条款", a["dup_clauses"], b["dup_clauses"]),
        ("零条款文件", a["zero_clause_docs"], b["zero_clause_docs"]),
        ("带页码条款占比", f"{a['page_ratio']:.1%}", f"{b['page_ratio']:.1%}"),
        ("最长条款字数", a["max_clause"], b["max_clause"]),
    ]
    print("| 指标 | 拼接式（旧） | 载体重建（新） |")
    print("|---|---|---|")
    for k, x, y in rows:
        print(f"| {k} | {x} | {y} |")
    print()
    print("主副本来源：", dict(b["carrier"]))
    diff = [(n, len(old[n].get("clauses") or []), len(new[n].get("clauses") or []))
            for n in new if n in old and
            len(old[n].get("clauses") or []) != len(new[n].get("clauses") or [])]
    print(f"条款数变化的文件：{len(diff)}")
    for n, x, y in sorted(diff, key=lambda t: t[2] - t[1])[:8]:
        print(f"  {n[:28]:30s} {x}→{y}")


if __name__ == "__main__":
    main()
