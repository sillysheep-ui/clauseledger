"""文档状态登记：把"没有这份文件"拆成四种不同的事。

为什么需要它：语料里查不到一份文件，可能是四种完全不同的情况——
抓过但被拒绝、列表里有还没抓、字节在但解析不出、以及真的从未存在。
前三种如果都显示成"没有"，就等于把"我们没拿到"说成"世界上没有"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DocStatus = Literal["ingested", "listed_only", "fetch_failed", "parse_failed",
                    "probably_exists"]

STATUS_MEANING = {
    "ingested": "已入库，有正文",
    "listed_only": "列表页出现过渡，但正文还没取到",
    "fetch_failed": "尝试抓取过，但没拿到字节",
    "parse_failed": "字节拿到了，但没能从里面抽出文字",
    "probably_exists": "编号区间里有缺口，不确定是否真发过",
}


@dataclass(frozen=True)
class DocEntry:
    doc_no: str
    title: str
    status: DocStatus
    url: str = ""
    chars: int = 0
    http_status: int | None = None
    error: str = ""
    attempts: int = 0
    last_tried: str = ""
    source: str = ""

    @property
    def unavailable(self) -> bool:
        """已知存在、但拿不到正文——绝不能当成"这项政策不存在"。"""
        return self.status != "ingested"

    def explain(self) -> str:
        """给人看的一句话。刻意不出现"暂无数据"这种把两件事混起来的话。"""
        head = f"《{self.title or self.doc_no}》" if self.title else f"{self.doc_no}"
        if self.status == "fetch_failed":
            code = f"HTTP {self.http_status}" if self.http_status else (self.error or "连接失败")
            tried = f"，试了 {self.attempts} 次" if self.attempts else ""
            when = f"，最后一次 {self.last_tried}" if self.last_tried else ""
            return (f"{head}（{self.doc_no}）我们知道它存在，但抓取失败（{code}{tried}{when}）。"
                    f"这不代表没有这份政策，只代表我们没能取到原文。")
        if self.status == "parse_failed":
            return (f"{head}（{self.doc_no}）原文已取到，但没能抽出文字"
                    f"（{self.error or '扫描件或表格型文件'}）。")
        if self.status == "probably_exists":
            return (f"{self.doc_no} 落在沪人社规的编号区间内，但官方栏目没有列出它。"
                    f"可能是站方未公开，也可能该号从未发布——我们还没能确认。")
        if self.status == "listed_only":
            return (f"{head}（{self.doc_no}）在列表里出现过，正文尚未抓取。")
        return f"{head}（{self.doc_no}）已入库，{self.chars} 字。"


def _tokens(text: str) -> set[str]:
    """粗分词：中文按二字滑窗，够用来判断"问题是不是在问这份文件"。"""
    text = "".join(ch for ch in text if ch.isalnum())
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


class DocRegistry:
    def __init__(self, entries: dict[str, DocEntry] | None = None):
        self._by_no = entries or {}

    @classmethod
    def load(cls, path: Path) -> "DocRegistry":
        entries: dict[str, DocEntry] = {}
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            entries[raw["doc_no"]] = DocEntry(**raw)
        return cls(entries)

    @classmethod
    def empty(cls) -> "DocRegistry":
        return cls({})

    def __len__(self) -> int:
        return len(self._by_no)

    def get(self, doc_no: str) -> DocEntry | None:
        return self._by_no.get(doc_no)

    def summary(self) -> dict[str, int]:
        out = {k: 0 for k in STATUS_MEANING}
        for e in self._by_no.values():
            out[e.status] += 1
        return out

    def best_gap(self, query_tokens: set[str], min_hits: int = 2) -> DocEntry | None:
        """在"已知但拿不到"的文件里，找与问题最相关的一份。"""
        q: set[str] = set()
        for t in query_tokens:
            q |= _tokens(t)
        best: tuple[int, DocEntry] | None = None
        for e in self._by_no.values():
            if not e.unavailable:
                continue
            hits = len(q & _tokens(e.title))
            if hits < min_hits:
                continue
            if best is None or hits > best[0]:
                best = (hits, e)
        return best[1] if best else None
