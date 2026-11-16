"""孤儿字节登记：盘上有、但没有记录指向的文件。

不删——删字节违反"只增不减"；但要登记，否则就是不可审计的垃圾。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

RAW = Path("data/raw")
PROCESSED = Path("data/processed")
OUT = Path("data/registry/orphans.jsonl")
PROBE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")  # 存盘时用域名命名的探测样本


def referenced_hashes(processed_dir: Path = PROCESSED) -> set[str]:
    """所有被记录引用过的字节（页面 + 附件），只取前 12 位。"""
    used: set[str] = set()
    for p in processed_dir.glob("*.json"):
        try:
            rec = json.loads(p.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        sha = rec.get("page_sha256")
        if sha:
            used.add(sha[:12])
        for a in rec.get("attachments", []):
            if a.get("sha256"):
                used.add(a["sha256"][:12])
        # 载体模型之后，没有被选为主副本的字节记在 carriers 里——它们不是孤儿。
        for c in rec.get("carriers") or []:
            if c.get("sha256"):
                used.add(c["sha256"][:12])
        cc = rec.get("carrier_choice") or {}
        for c in (cc.get("supplements") or []) + (cc.get("dropped") or []):
            if c.get("sha256"):
                used.add(c["sha256"][:12])
    return used


def scan(raw_dir: Path = RAW, processed_dir: Path = PROCESSED) -> list[dict]:
    used = referenced_hashes(processed_dir)
    out: list[dict] = []
    if not raw_dir.exists():
        return out
    for d in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        if d.name in used:
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            out.append({
                "sha12": d.name, "name": f.name, "bytes": f.stat().st_size,
                "kind": ("probe" if PROBE.match(f.name) else "orphan"),
            "reason": "probe_sample_not_in_corpus" if PROBE.match(f.name)
                      else "no_record_points_here",
                "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
    return out


def write(rows: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
                   if rows else "", "utf-8")


def main() -> None:
    rows = scan()
    write(rows)
    total = sum(r["bytes"] for r in rows)
    print(f"孤儿 {len(rows)} 个 / {total:,} B → {OUT}")
    for r in rows:
        print(f"  {r['sha12']}/{r['name']}  {r['bytes']:,} B")


if __name__ == "__main__":
    main()
