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
from functools import lru_cache

from app.retrieval.records import Clause

SENT = re.compile(r"[^。；！？\n]{1,400}[。；！？]?")
# 断头句：条款以指代开头（"上述人员""该条""下列单位"），或结尾是冒号，
# 说明它依赖上一段。这时把同文件紧邻的前一条也放进证据，否则模型只能猜。
BACKREF = re.compile(r"^(上述|该|本条|本款|此项|此条|下列|其中|前款|前述|以上)")
QUOTED = re.compile(r"《([^》]{4,60})》")
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
    heading: str = ""           # 最近一层祖先条款的第一行：交付时告诉人"这条在讲哪一节"

    @property
    def context(self) -> str:
        """交付时给人/给模型看的一行：这份文件叫什么、这条在哪一节。"""
        bits = [self.title] if self.title else []
        if self.heading:
            bits.append(self.heading)
        elif self.path and str(self.path[0]).startswith("附件"):
            bits.append(str(self.path[0]))
        return " · ".join(bits)

    def block(self) -> str:
        head = f"[{self.eid}] {self.citation}  （{self.role}）"
        ctx = self.context
        if ctx:
            head += f"\n上下文：{ctx}"
        return f"{head}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def _trim(text: str, limit: int = MAX_ONE) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = list(SENT.finditer(cut))
    return (cut[: ends[-1].end()] if ends else cut) + "…"


@lru_cache(maxsize=1)
def _by_doc_path() -> dict[tuple[str, tuple], Clause]:
    return {(c.doc_no, tuple(c.path)): c for c in _clauses()}


def heading_for(doc_no: str, path: list[str]) -> str:
    """交付时的上下文：最近一层祖先条款的第一行。

    先按 path 找祖先；找不到就退回**区间包含**——附件里的「一、」常常没有独立成条
    （只有「附件4/一、/（一）/1.」这样的叶子），按路径找会全空。
    只取第一行：条款第一行就是它自己的标题，取整段等于把父条重复一遍。
    """
    if not doc_no or not path:
        return ""
    idx = _by_doc_path()
    for cut in range(len(path) - 1, 0, -1):
        anc = idx.get((doc_no, tuple(path[:cut])))
        if anc and anc.text:
            first = anc.text.split("\n", 1)[0].strip()
            if first:
                return first[:40]
    me = idx.get((doc_no, tuple(path)))
    if me is None:
        return ""
    best = None
    for other in _clauses():
        if other.doc_no != doc_no or other.id == me.id:
            continue
        if other.start <= me.start and other.end >= me.end:
            if best is None or (other.end - other.start) < (best.end - best.start):
                best = other
    if best and best.text:
        return best.text.split("\n", 1)[0].strip()[:40]
    return ""


def _title_index() -> dict[str, str]:
    """规范化标题 → 文号。用于"按《X》执行"这类跨文件引用的 1 跳。"""
    return _title_index_cached()


@lru_cache(maxsize=1)
def _title_index_cached() -> dict[str, str]:
    out: dict[str, str] = {}
    for c in _clauses():
        t = re.sub(r"[\s《》]", "", c.title or "")
        if len(t) >= 6:
            out.setdefault(t, c.doc_no)
    return out


def _refs_in(text: str, own: str) -> list[str]:
    """正文里点名引用的文件（按标题），最多两条，且排除自己。"""
    idx = _title_index()
    found: list[str] = []
    for m in QUOTED.finditer(text or ""):
        name = re.sub(r"[\s《》]", "", m.group(1))
        if len(name) < 6:
            continue
        for title, no in idx.items():
            if no == own:
                continue
            if name in title or title in name:
                if no not in found:
                    found.append(no)
                break
        if len(found) >= 2:
            break
    return found


def _clauses() -> list[Clause]:
    from app.retrieval import search as S

    return S._load()[1]


def _legacy_assemble(question: str, hits, *, max_chars: int = 12000) -> list[Evidence]:
    """旧版：hit → parent → neighbor。保留作对照（CLAUSELEDGER_ASSEMBLE=legacy）。"""
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
                            list(c.path), c.page, t, role, c.citation,
                            heading_for(c.doc_no, list(c.path))))
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


def _qterms(question: str) -> set[str]:
    from app.retrieval.index import tokenize

    return {t for t in tokenize(question) if len(t) >= 2}


def _rel(c: Clause, qt: set[str], *, numeric: bool) -> float:
    text = c.text or ""
    hit = sum(1 for t in qt if t in text)
    score = float(hit)
    if numeric and re.search(r"\d", text):
        score += 1.5
    score += min(len(text), 400) / 4000.0      # 轻微偏长，避免只给一句残句
    return score


def assemble(question: str, hits, *, max_chars: int = 12000,
             anchors: int | None = None, per_doc: int | None = None) -> list[Evidence]:
    """锚点 -> 同文件展开 -> 预算内贪心。

    和旧版（hit → parent → neighbor）的区别：旧版只沿"位置"扩一条，这一版
    沿"文件"扩——同一份文件里跟问题最相关的几条都给它，因为答案句经常
    不在命中的那一条上，也不是它的父条或邻居。
    """
    import os

    from app.retrieval.answertype import detect

    if os.environ.get("CLAUSELEDGER_ASSEMBLE") == "legacy":
        return _legacy_assemble(question, hits, max_chars=max_chars)

    anchors = int(os.environ.get("CLAUSELEDGER_ASM_ANCHORS") or anchors or 3)
    prev_on = os.environ.get("CLAUSELEDGER_ASM_PREV", "1") != "0"
    ref_on = os.environ.get("CLAUSELEDGER_ASM_REF", "1") != "0"
    # 20 不是"最优参数"，是"这份文件的条款基本都给"：实测 3 → 67.1%、5 → 68.6%、
    # 8 → 70.0%、20 → 72.9%，而 12,000 字预算到 20 才接近用满。
    per_doc = int(os.environ.get("CLAUSELEDGER_ASM_PER_DOC") or per_doc or 20)

    clauses = _clauses()
    by_doc: dict[tuple[str, int], list[Clause]] = {}
    for c in clauses:
        by_doc.setdefault((c.doc_no, c.part), []).append(c)
    for v in by_doc.values():
        v.sort(key=lambda c: c.start)

    qt = _qterms(question)
    numeric = detect(question) == "num"

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
                            list(c.path), c.page, t, role, c.citation,
                            heading_for(c.doc_no, list(c.path))))
        used += len(t)
        return True

    # 1) 锚点：按命中顺序取前 N 份不同的文件
    anchor_hits, docs = [], []
    for h in hits:
        if h.clause.doc_no not in docs:
            docs.append(h.clause.doc_no)
            anchor_hits.append(h)
        if len(anchor_hits) >= anchors:
            break

    for h in anchor_hits:
        c = h.clause
        add(c, "hit", (h.child or None))

        if prev_on:                             # 断头句：把紧邻的前一条也带上
            probe = (h.child or c.text or "").strip()
            if BACKREF.match(probe) or probe.rstrip().endswith(("：", ":")):
                sibs = by_doc.get((c.doc_no, c.part), [])
                i = next((k for k, x in enumerate(sibs) if x.id == c.id), None)
                if i:
                    add(sibs[i - 1], "prev")

        if len(c.path) >= 2:                    # 父条先给："上述人员""除…外"都在父条里
            want = tuple(c.path[:-1])
            for cand in by_doc.get((c.doc_no, c.part), []):
                if tuple(cand.path) == want and cand.start <= c.start <= max(cand.end, c.start):
                    add(cand, "parent")
                    break

        if ref_on:                              # 正文点名引用别的文件：补 1 条
            for no in _refs_in(c.text or "", c.doc_no):
                sibs2 = by_doc.get((no, c.part), []) or by_doc.get((no, 0), [])
                if not sibs2:
                    continue
                best = max(sibs2, key=lambda x: _rel(x, qt, numeric=numeric))
                add(best, "ref")

        # 同文件展开：按与问题的相关度取前 per_doc 条（含锚点已加的不重复）
        pool = [x for x in by_doc.get((c.doc_no, c.part), []) if x.id not in seen]
        pool.sort(key=lambda x: (-_rel(x, qt, numeric=numeric), x.start))
        for cand in pool[:per_doc]:
            add(cand, "same_doc")

    return out
