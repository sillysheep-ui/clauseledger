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
DERIVED = Path("data/derived/validity.json")

# 派生文件按 mtime 缓存：定时任务重算之后，常驻进程不用重启就能看到新时间段。
_PERIODS: tuple[float, dict] | None = None


def _derived() -> dict:
    global _PERIODS
    try:
        mtime = DERIVED.stat().st_mtime
    except OSError:
        return {}
    if _PERIODS is None or _PERIODS[0] != mtime:
        _PERIODS = (mtime, json.loads(DERIVED.read_text("utf-8")))
    return _PERIODS[1]


def repeals_of(doc_no: str) -> list[dict]:
    """这张时间表里记着的废止关系（和延期边来自同一个派生文件）。"""
    return (_derived().get("repeals") or {}).get(doc_no) or []


def periods_of(doc_no: str) -> list[dict]:
    """这份文件的有效期区间（按时间排序）。没有派生数据就返回空。"""
    return (_derived().get("periods") or {}).get(doc_no) or []


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


def status_for(doc_no: str, as_of: str | None = None,
               rels: dict[str, list[dict]] | None = None,
               doc: dict | None = None) -> Validity:
    """按**时间段**判效力：同一天走一个区间，断档就是断档。

    和 `status_at` 的区别：这里不读 `effective_to` 单值，而是读派生出来的
    「有效期区间」——文件 2020 年到期、2026 年才被延到 2031 年时，2021 年
    落在断档里，必须判失效，而不是拿 2031 当年底。
    """
    day = as_of or date.today().isoformat()
    rels = rels if rels is not None else load()
    no = doc_no or (doc or {}).get("doc_no") or ""

    for r in sorted(repeals_of(no), key=lambda x: x["date"]):
        if r["date"] <= day:
            kind = "partially_repealed" if r["kind"] == "partial_repeal" else "repealed"
            return Validity(kind, day, f"被《{r['by']}》"
                            f"{'部分条款停止执行' if kind == 'partially_repealed' else '废止'}"
                            f"（{r['date']} 起）", repealed_by=r["by"], since=r["date"])

    # 派生文件缺这份文件时，退回关系表（同一批数据的另一份副本，不是第二套规则）。
    for e in rels.get(no, []):
        since = e.get("effective_date")
        if since and since <= day:
            kind = "partially_repealed" if e["type"] == "partial_repeal" else "repealed"
            return Validity(kind, day, f"被《{e['source']}》"
                            f"{'部分条款停止执行' if kind == 'partially_repealed' else '废止'}"
                            f"（{since} 起）", repealed_by=e["source"], since=since)

    segs = periods_of(no)
    if not segs:
        return status_at(doc or {"doc_no": no}, as_of=day, rels=rels)

    first = segs[0]
    if first.get("start") and day < first["start"]:
        return Validity("not_yet", day, f"尚未施行（{first['start']} 起）")

    for i, seg in enumerate(segs):
        start, end = seg.get("start") or "", seg.get("end") or ""
        if start and day < start:
            # 落在上一段结束到这一段开始之间：断档
            prev = segs[i - 1]
            return Validity("expired", day,
                            f"断档：上一段有效期至 {prev.get('end')}，"
                            f"这一段从 {start} 起", since=prev.get("end"))
        if not end or day <= end:
            # 只提"这一天之前已经发生"的延期：问 2019 年的事，不该拿 2020 年的通知当理由。
            pairs = list(zip(seg.get("by") or [],
                             seg.get("by_on") or [""] * len(seg.get("by") or [])))
            end = seg.get("end") or ""
            relevant = [b for b, on in pairs
                        if (not on or on <= day) and (not end or not on or on <= end)]
            if not end:
                # 没写到期日 ≠ 永久有效：标 unknown，检索保留、但不给"现行"加权。
                return Validity("unknown", day, "未记载有效期")
            why = f"经《{relevant[-1]}》延期" if relevant else ""
            return Validity("effective", day, f"{why}有效期至 {end}".strip())

    last = segs[-1]
    return Validity("expired", day, f"有效期至 {last.get('end')}，已过期",
                    since=last.get("end"))


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


YEAR = __import__("re").compile(r"(?<!\d)(19\d{2}|20\d{2})\s*年?")


def year_of(text: str) -> int | None:
    """从提问里找年份。"2020年工伤怎么赔" → 2020。

    只认 19xx/20xx 且前后不接数字，避免把文号里的年份、金额、比例当年份。
    """
    if not text:
        return None
    m = YEAR.search(text)
    if not m:
        return None
    y = int(m.group(1))
    return y if 1990 <= y <= 2035 else None


def window(doc: dict, rels: dict[str, list[dict]] | None = None) -> tuple[str | None, str | None]:
    """文件真正有效的区间 [start, end)。

    起点取 effective_from，没有就用**成文日期**当兜底——
    文件不可能在自己成文之前生效，所以成文日期是一个安全的下界
    （唯一例外是"自某年起施行"的溯及既往，那种文件正文里通常写明了 effective_from，不会走到兜底）。
    终点取 min(effective_to, 废止日)：废止日是语料里明确写着的，比"有效期至"更硬。
    """
    start = doc.get("effective_from") or doc.get("issued_at")
    end = doc.get("effective_to")
    if rels:
        for e in rels.get(doc.get("doc_no") or "", []):
            since = e.get("effective_date")
            if since and (end is None or since < end):
                end = since
    return start, end


def in_force(doc: dict, year: int, rels: dict[str, list[dict]] | None = None) -> bool:
    """这一年里有没有效（区间与年份有交集即算）。"""
    segs = periods_of(doc.get("doc_no") or "")
    if segs:
        lo, hi = f"{year}-01-01", f"{year}-12-31"
        for seg in segs:
            start, end = seg.get("start") or "0000", seg.get("end") or "9999"
            if start <= hi and end >= lo:
                return True
        return False
    start, end = window(doc, rels)
    lo, hi = f"{year}-01-01", f"{year}-12-31"
    if start and start > hi:
        return False
    if end and end < lo:
        return False
    return True
