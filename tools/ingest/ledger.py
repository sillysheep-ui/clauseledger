"""失败台账：把散落在多次运行里的抓取尝试，按文件汇到一张表。

为什么需要它：每次批处理写一个 `data/manifests/ingest-*.jsonl`，
同一份文件第一次失败、第二次成功，两条记录躺在两个文件里，
**没有任何东西把它们串起来**。于是答不了：
    这份文件一共试过几次？上次失败是什么时候？是偶尔抽风还是稳定抓不到？

产物：
    data/registry/attempts.jsonl   一行一个文件，含尝试历史摘要
    data/reports/attempts.md       给人看的台账与结论

判据（很重要）：**"失败过"≠"现在不可达"**。
    只要有任意一次成功，state 就是 `ok`，历史失败只作为记录保留。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

MANIFESTS = Path("data/manifests")
OUT = Path("data/registry/attempts.jsonl")
REPORT = Path("data/reports/attempts.md")
TS = re.compile(r"(\d{8}T\d{6})")


def _ts_of(path: Path) -> str:
    m = TS.search(path.name)
    return m.group(1) if m else ""


@dataclass
class Attempt:
    key: str
    attempts: int = 0
    ok_ever: bool = False
    first_failed: str = ""
    last_tried: str = ""
    last_ok: str = ""
    last_status: int | None = None
    last_stage: str = ""
    last_error: str = ""
    runs: int = 0

    @property
    def state(self) -> str:
        if self.ok_ever:
            return "ok" if not self.first_failed else "ok_after_retry"
        return "never_ok"

    def explain(self) -> str:
        if self.state == "ok":
            return "一次就成"
        if self.state == "ok_after_retry":
            return f"失败过（{self.first_failed}），后来成功（{self.last_ok}）"
        why = self.last_error or (f"HTTP {self.last_status}" if self.last_status else "未知")
        return f"从未成功，最后状态 {self.last_status or '—'}（{why}），已试 {self.attempts} 次"


def load_rows(manifests: Path = MANIFESTS) -> list[tuple[str, dict]]:
    """返回 [(时间戳, 行)]，只读 ingest-*.jsonl。"""
    rows: list[tuple[str, dict]] = []
    for p in sorted(manifests.glob("ingest-*.jsonl")):
        ts = _ts_of(p)
        for line in p.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("doc_no"):
                rows.append((ts, rec))
    return rows


def aggregate(rows: list[tuple[str, dict]]) -> dict[str, Attempt]:
    out: dict[str, Attempt] = {}
    for ts, rec in rows:
        key = rec["doc_no"]
        a = out.setdefault(key, Attempt(key=key))
        a.attempts += 1
        a.last_tried = max(a.last_tried, ts)
        if rec.get("ok"):
            a.ok_ever = True
            a.last_ok = max(a.last_ok, ts)
        else:
            if not a.first_failed:
                a.first_failed = ts
            a.last_status = rec.get("http_status")
            a.last_stage = rec.get("stage") or ("httpx" if rec.get("error") == "FetchError" else "")
            a.last_error = rec.get("msg") or rec.get("error") or ""
    return out


def main() -> None:
    rows = load_rows()
    table = aggregate(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for a in sorted(table.values(), key=lambda x: x.last_tried, reverse=True):
            d = asdict(a)
            d["state"] = a.state
            d["explain"] = a.explain()
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    never = [a for a in table.values() if not a.ok_ever]
    retried = [a for a in table.values() if a.ok_ever and a.first_failed]
    lines = ["# 抓取台账", "",
             f"- 涉及文件：**{len(table)}**",
             f"- 从未成功：**{len(never)}**",
             f"- 失败过但后来成功：**{len(retried)}**",
             f"- 一次就成：**{len(table) - len(never) - len(retried)}**", ""]
    if never:
        lines += ["## 从未成功（需要人决定：换源 / 浏览器 / 放弃）", "",
                  "| 文件 | 次数 | 最后状态 | 阶段 | 说明 |", "|---|---:|---:|---|---|"]
        for a in sorted(never, key=lambda x: x.last_tried, reverse=True)[:40]:
            lines.append(f"| {a.key} | {a.attempts} | {a.last_status or '—'} | "
                         f"{a.last_stage or '—'} | {a.last_error[:60]} |")
    if retried:
        lines += ["", "## 失败后成功（证明「失败」不等于「不可达」）", "",
                  "| 文件 | 首次失败 | 后来成功 |", "|---|---|---|"]
        for a in sorted(retried, key=lambda x: x.last_ok, reverse=True)[:20]:
            lines.append(f"| {a.key} | {a.first_failed} | {a.last_ok} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"{len(table)} 文件 / 从未成功 {len(never)} / 失败后成功 {len(retried)} → {OUT}")


if __name__ == "__main__":
    main()
