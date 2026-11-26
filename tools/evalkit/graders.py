"""三个确定性打分器：条款召回 / 证据覆盖 / 拒答。

约定（见 docs/EVAL-SPEC.md §1）：
* 分母是"该 split 的全部题"，失败算失败，不做条件化剔除；
* 金标是**条款**，先离线定位"哪一条在讲答案"，再看它排第几；
* 只有"金标文件里没有任何一条覆盖官方答案句"才记 stale_gold，单列不算失败。
"""
from __future__ import annotations

from collections import defaultdict

from app.answer import context as C
from app.retrieval import search as S
from tools.eval_answer import covered_sentences

RANK_K = 50
_BY_DOC: dict[str, list] | None = None
_GOLD: dict[str, tuple[str | None, str]] = {}


def _by_doc() -> dict[str, list]:
    global _BY_DOC
    if _BY_DOC is None:
        _ret, clauses, _kids = S._load()
        idx: dict[str, list] = defaultdict(list)
        for c in clauses:
            idx[c.doc_no].append(c)
        _BY_DOC = idx
    return _BY_DOC


def gold_clause_id(q: dict, answer: str) -> tuple[str | None, str]:
    """在 gold_doc 的条款里找"最能覆盖官方答案句"的那一条。"""
    key = q["id"]
    if key in _GOLD:
        return _GOLD[key]
    if not answer:
        _GOLD[key] = (None, "no_answer")
        return _GOLD[key]
    best, best_cov = None, 0
    for c in _by_doc().get(q.get("gold_doc"), []):
        cov, _ = covered_sentences(answer, c.text)
        if cov > best_cov or (cov == best_cov and cov and best and len(c.text) < len(best.text)):
            best, best_cov = c, cov
    _GOLD[key] = (best.id if best else None, "ok" if best else "stale_gold")
    return _GOLD[key]


def _covers(answer: str, hits: list) -> bool:
    blob = "".join(h.clause.text for h in hits)
    return covered_sentences(answer, blob)[0] > 0 if answer else False


def gold_present(q: dict, answer: str) -> bool:
    """金标文件里到底有没有一条叶子覆盖官方答案句——没有就是语料缺口。"""
    blob = "".join(c.text for c in _by_doc().get(q.get("gold_doc"), []))
    return covered_sentences(answer, blob)[0] > 0 if answer else False


def clause_rank(q: dict, answer: str, hits: list | None = None) -> tuple[int | None, str]:
    """返回 (最小 k, state)：第几条起"取前 k 条并起来"能覆盖官方答案句。

    索引是叶子条款，一条答案句常常跨 2–3 条，所以按"并集覆盖"算，而不是要求
    某一条必须整句包含答案——后者会把父条款（不进索引）误判成失败。
    """
    if not answer:
        return None, "no_answer"
    if not gold_present(q, answer):
        return None, "stale_gold"
    hs = hits if hits is not None else S.ranked_clauses(q.get("q_ctx") or q["q"], k=RANK_K)
    if not _covers(answer, hs):
        return None, "miss"
    lo, hi = 1, len(hs)
    while lo < hi:                      # 二分最小的 k
        mid = (lo + hi) // 2
        if _covers(answer, hs[:mid]):
            hi = mid
        else:
            lo = mid + 1
    return lo, "ok"


def assemble_from(hits: list, k: int = 30) -> list:
    return C.assemble_flat(hits[:k])


def answer_evidence_coverage(q: dict, answer: str, hits: list | None = None) -> dict:
    hs = hits if hits is not None else S.ranked_clauses(q.get("q_ctx") or q["q"], k=RANK_K)
    ev = assemble_from(hs)
    blob = "".join(e.text for e in ev)
    covered, total = covered_sentences(answer, blob) if answer else (0, 0)
    return {"covered": covered, "sentences": total, "hit": bool(covered),
            "items": len(ev), "chars": len(blob)}


def refusal(q: dict) -> dict:
    a = S.search_result(q.get("q_ctx") or q["q"], k=10)
    return {"outcome": a.outcome, "reason": a.reason, "kind": a.kind}
