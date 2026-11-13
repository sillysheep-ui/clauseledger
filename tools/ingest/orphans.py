"""孤儿字节登记：盘上有、但没有记录指向的文件。

不删——删字节违反"只增不减"；但要登记，否则就是不可审计的垃圾。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

RAW = Path("data/raw")
PROCESSED = Path("data/processed")
OUT = Path("data/registry/orphans.jsonl")


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
                "reason": "no_record_points_here",
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
