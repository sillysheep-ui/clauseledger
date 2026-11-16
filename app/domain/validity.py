"""效力判定：这份文件在**某一天**是不是还有效。

为什么单独一层：
1. 文件自身的字段只写了「有效期至」，但"被别的文件废止"写在别人身上。
   实测 `沪人社福发〔2015〕34号` 的 `effective_to` 是空，看起来仍有效，
   实际已被 `沪人社规〔2020〕26号` 在 2020-12-01 废止——一条漏记让 9 道题判错。
2. 效力是**时间的函数**。问"2020 年上海工伤怎么赔"，答案要用当年有效的文件；
   用今天的效力去判 2020 年的问题，是实质错误。所以接口必须带 `as_of`。

状态优先级：repealed > expired > unknown > effective。
"废止"是明确的动作；"过期"是按日期推的；"不知道"不等于"失效"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RELATIONS = Path("data/registry/relations.jsonl")


@dataclass(frozen=True)
class Validity:
    status: str          # effective | expired | repealed | partially_repealed | unknown
    as_of: str
    reason: str = ""
    repealed_by: str | None = None
    since: str | None = None


def load(path: Path | str = RELATIONS) -> dict[str, list[dict]]:
    """按**被废止方**建索引：doc_no -> 指向它的废止/延期事件。"""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for line in p.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("target") and e.get("type") in ("repeal", "partial_repeal"):
            out.setdefault(e["target"], []).append(e)
    return out


def status_at(doc: dict, *, as_of: str | None = None,
              rels: dict[str, list[dict]] | None = None) -> Validity:
    """判定一份文件在 as_of 那天的效力。

    doc 用 processed 记录即可（只用 doc_no / effective_from / effective_to）。
    """
    day = as_of or date.today().isoformat()
    rels = rels if rels is not None else load()
    no = doc.get("doc_no") or ""

    # 1) 被废止（只算已经生效的废止事件；未来才生效的不算）
    for e in rels.get(no, []):
        since = e.get("effective_date")
        if since and since <= day:
            kind = "partially_repealed" if e["type"] == "partial_repeal" else "repealed"
            why = "部分条款停止执行" if kind == "partially_repealed" else "废止"
            return Validity(kind, day,
                            f"被《{e['source']}》{why}（{since} 起）",
                            repealed_by=e["source"], since=since)

    # 2) 自身有效期
    start, end = doc.get("effective_from"), doc.get("effective_to")
    if start and start > day:
        return Validity("unknown", day, f"尚未施行（{start} 起）")
    if end and end < day:
        return Validity("expired", day, f"有效期至 {end}，已过期", since=end)

    # 3) 没有终止日期就是"不知道"，不是"有效"
    if not end:
        return Validity("unknown", day, "未记载有效期，也未查到废止文件")
    return Validity("effective", day, f"有效期至 {end}")


def repealed_map(*, as_of: str | None = None,
                 rels: dict[str, list[dict]] | None = None) -> dict[str, str]:
    """今天是废止状态的 doc_no -> 理由。检索时用来降权。"""
    day = as_of or date.today().isoformat()
    rels = rels if rels is not None else load()
    out: dict[str, str] = {}
    for no, edges in rels.items():
        for e in edges:
            since = e.get("effective_date")
            if since and since <= day:
                out[no] = f"被《{e['source']}》废止（{since} 起）"
                break
    return out
