"""给每道官方题定一个 as_of：解读文章发布那天。

为什么不是「今天」：官方解读文章是 2023 年写的，问的是 2023 年的政策状态。
拿今天的状态去判它「引用了失效文件」，是把两个时间混在一起——文章没错，是我们错。

as_of 优先取 `source_url` 里的发布日期（`/YYYYMMDD/`），取不到再退回 `source`。
金标文件在 as_of 当天的状态分四类：
  in_force   —— 当时有效，A 口径可测
  expired    —— 当时已过有效期（政策换版 / 延期链缺边），premise 已破
  not_yet    —— 当时还没生效（解读文章先于施行日），premise 已破
  unknown    —— 施行日期缺失，无法判断

输出 `data/eval/asof.json`，供 `tools/eval_asof.py` 使用。
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

QS = "eval/questions_v3.json"
OUT = Path("data/eval/asof.json")
RE_DATE = re.compile(r"/(\d{4})(\d{2})(\d{2})/")


def load_docs() -> dict[str, dict]:
    docs = {}
    for p in Path("data/processed").glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        if d.get("doc_no"):
            docs[d["doc_no"]] = d
    return docs


def status_at(doc: dict, as_of: str) -> str:
    start = str(doc.get("effective_from") or doc.get("issued_at") or "")[:10]
    end = str(doc.get("valid_until") or doc.get("effective_to") or "")[:10]
    if not start:
        return "unknown"
    if start > as_of:
        return "not_yet"
    if end and end < as_of:
        return "expired"
    return "in_force"


def main() -> None:
    qs = [q for q in json.loads(Path(QS).read_text("utf-8"))
          if q.get("category") == "official" and not q.get("ambiguous") and not q.get("stale")]
    docs = load_docs()
    rows: dict[str, dict] = {}
    stat = collections.Counter()
    for q in qs:
        url = q.get("source_url") or q.get("source") or ""
        m = RE_DATE.search(url)
        as_of = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        d = docs.get(q["gold_doc"]) or {}
        st = status_at(d, as_of) if as_of else "no_date"
        rows[q["id"]] = {
            "id": q["id"],
            "split": q.get("split"),
            "gold_doc": q["gold_doc"],
            "as_of": as_of,
            "as_of_src": "url" if m else "none",
            "gold_effective_from": str(d.get("effective_from") or d.get("issued_at") or "")[:10] or None,
            "gold_valid_until": str(d.get("valid_until") or d.get("effective_to") or "")[:10] or None,
            "gold_status_at_asof": st,
            "stale_asof": st != "in_force",
        }
        stat[st] += 1
        stat[f"split:{q.get('split')}:{st}"] += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    total = len(rows)
    print(f"题目 {total} 道，写入 {OUT}")
    for k in ("in_force", "expired", "not_yet", "unknown", "no_date"):
        print(f"  {k:<9}{stat[k]:>4}")
    print(f"  可测（in_force）{stat['in_force']}/{total} = {stat['in_force']/total:.0%}")


if __name__ == "__main__":
    main()
