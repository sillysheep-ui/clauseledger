"""把"检索到的几条"装配成给模型的证据包。

为什么单独一层：
- 检索返回的是**条款**（一句一条），模型要读的是**段落级语境**——父条治"上述人员"，
  邻条治"除…外"，同一份文件的其他版本治"哪一版在生效"。
- 装配必须有预算。10k token 装得下，但装进去的每条都要有角色（hit/parent/neighbor），
  否则超长上下文只会让模型挑错条款。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.retrieval.records import Clause

SENT = re.compile(r"[^。；！？\n]{1,400}[。；！？]?")
MAX_ONE = 1200          # 单条证据上限（字）
CHARS_PER_TOKEN = 1.5   # 中文粗估：1 token ≈ 1.5 字


@dataclass
class Evidence:
    eid: str
    clause_id: str
    doc_no: str
    title: str
    path: list[str] = field(default_factory=list)
    page: int = 0
    text: str = ""
    role: str = "hit"           # hit | parent | neighbor
    citation: str = ""

    def block(self) -> str:
        head = f"[{self.eid}] {self.citation}  （{self.role}）"
        return f"{head}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def _trim(text: str, limit: int = MAX_ONE) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = list(SENT.finditer(cut))
    return (cut[: ends[-1].end()] if ends else cut) + "…"


def _clauses() -> list[Clause]:
    from app.retrieval import search as S

    return S._load()[1]


def assemble(question: str, hits, *, max_chars: int = 12000) -> list[Evidence]:
    """按 hit → parent → neighbor 的顺序填，填满预算即停。"""
    clauses = _clauses()
    by_doc: dict[tuple[str, int], list[Clause]] = {}
    for c in clauses:
        by_doc.setdefault((c.doc_no, c.part), []).append(c)
    for v in by_doc.values():
        v.sort(key=lambda c: c.start)

    out: list[Evidence] = []
    seen: set[str] = set()
    used = 0

    def add(c: Clause, role: str, text: str | None = None) -> bool:
        nonlocal used
        if c.id in seen:
            return False
        t = _trim(text if text is not None else c.text)
        if used + len(t) > max_chars:
            return False
        seen.add(c.id)
        out.append(Evidence(f"E{len(out) + 1}", c.id, c.doc_no, c.title,
                            list(c.path), c.page, t, role, c.citation))
        used += len(t)
        return True

    for h in hits:
        add(h.clause, "hit", (h.child or None))

    for h in hits:                              # 父条：同一文件、路径少一级、区间包含它
        c = h.clause
        if len(c.path) < 2:
            continue
        want = tuple(c.path[:-1])
        for cand in by_doc.get((c.doc_no, c.part), []):
            if tuple(cand.path) == want and cand.start <= c.start <= max(cand.end, c.start):
                add(cand, "parent")
                break

    for h in hits:                              # 邻条：同文件按位置前后各一条
        c = h.clause
        sibs = by_doc.get((c.doc_no, c.part), [])
        for i, cand in enumerate(sibs):
            if cand.id != c.id:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(sibs):
                    add(sibs[j], "neighbor")
            break

    return out


def estimate_tokens(ev: list[Evidence]) -> int:
    return int(sum(len(e.text) for e in ev) / CHARS_PER_TOKEN)


def render(ev: list[Evidence]) -> str:
    return "\n\n".join(e.block() for e in ev)
