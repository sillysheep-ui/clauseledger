"""Agent 能调用的动作，进程内实现。

为什么单独一层：
- AgentScope 的工具体现在是「同进程函数」，将来的 HTTP/socket 外壳只换 transport，
  不改这里的语义；
- 索引、模型、条款缓存都是模块级单例，**进程起一次、热调用 0.1 秒级**——这正是
  spike 里 24 秒/次的来源（每次 subprocess 重新加载 torch + bge）。

所有函数只返回可 JSON 序列化的 dict，不返回 Clause/Cand 对象：工具层是边界。
证据统一走 `app.answer.context.assemble()`，与 11 步 pipeline 完全同一份。
"""
from __future__ import annotations

import json
from typing import Any

_CACHE: dict[str, Any] = {}


def _search(question: str, k: int, as_of: str | None):
    from app.retrieval.search import search_result

    return search_result(question, k=k, as_of=as_of)


def _ev_dict(e) -> dict:
    return {
        "eid": e.eid,
        "doc_no": e.doc_no,
        "title": e.title,
        "citation": e.citation,
        "page": e.page,
        "path": list(e.path),
        "role": e.role,
        "heading": e.heading,
        "context": e.context,
        "text": e.text,
    }


def coverage(question: str, city: str | None = None) -> dict:
    """这条问题覆盖得到吗。返回 status（covered/partial/blocked/out_of_scope）、
    allows_retrieval、以及一句给人看的说明。"""
    from app.domain.coverage import CoverageIndex
    from app.retrieval.search import detect_city

    idx = _CACHE.get("coverage")
    if idx is None:
        idx = _CACHE["coverage"] = CoverageIndex.load("data/coverage.json")
    c = idx.check(city or detect_city(question) or "上海", None)
    return {
        "status": c.status,
        "city": c.city,
        "allows_retrieval": bool(c.allows_retrieval),
        "message": c.message,
    }


def evidence(question: str, k: int = 5, as_of: str | None = None) -> dict:
    """检索并装配证据（3 个锚点文件 + 父条 + 同文件相关条款），每条带文号/路径/页码/上下文。

    和 pipeline 用的是同一个 assemble()，所以 agent 与 pipeline 看到的证据逐条一致。
    若覆盖不到或证据不足，refused=true，并说明原因与检索范围。
    """
    a = _search(question, k, as_of)
    if a.refused:
        return {
            "refused": True,
            "kind": a.kind,
            "reason": a.reason,
            "searched": {"n_docs": a.n_docs, "n_candidates": a.n_candidates},
            "as_of": a.as_of,
            "evidence": [],
        }
    from app.answer.context import assemble

    ev = assemble(question, a.hits)
    return {
        "refused": False,
        "kind": a.kind,
        "as_of": a.as_of,
        "searched": {"n_docs": a.n_docs, "n_candidates": a.n_candidates},
        "evidence": [_ev_dict(e) for e in ev],
    }


def search(question: str, k: int = 5, as_of: str | None = None) -> dict:
    """只检索不装配（调试用）：按分数返回原始命中条款。"""
    a = _search(question, k, as_of)
    return {
        "refused": bool(a.refused),
        "kind": a.kind,
        "reason": a.reason if a.refused else None,
        "as_of": a.as_of,
        "n_docs": a.n_docs,
        "n_candidates": a.n_candidates,
        "hits": [
            {
                "citation": h.clause.citation,
                "doc_no": h.clause.doc_no,
                "page": h.clause.page,
                "status": h.clause.status,
                "score": round(float(h.score), 2),
                "text": h.clause.text[:800],
            }
            for h in a.hits
        ],
    }


def get_clause(doc_no: str, path: list[str] | None = None, page: int | None = None) -> dict:
    """按文号 + 路径（或页码）取条款原文；找不到就 found=false，不要编造。"""
    from app.retrieval.search import _load

    _ret, clauses, _children = _load()
    key = tuple(path or [])
    for c in clauses:
        if c.doc_no != doc_no:
            continue
        if key and tuple(c.path) != key:
            continue
        if page and c.page != page:
            continue
        return {
            "found": True,
            "doc_no": c.doc_no,
            "title": c.title,
            "citation": c.citation,
            "path": list(c.path),
            "page": c.page,
            "status": c.status,
            "text": c.text,
        }
    return {"found": False, "doc_no": doc_no, "path": list(path or [])}


def doc_exists(doc_no: str) -> dict:
    """这个文号在语料里吗。用于防止模型写出不存在的文号。"""
    from app.retrieval.search import _load

    _ret, clauses, _children = _load()
    for c in clauses:
        if c.doc_no == doc_no:
            return {"exists": True, "doc_no": doc_no, "title": c.title}
    return {"exists": False, "doc_no": doc_no}


def judge_luohu(profile: dict) -> dict:
    """上海落户评分判定：入参是画像槽位，出参是四态判定 + 已知分/上限/差距。"""
    from app.judgement.luohu import evaluate

    v = evaluate(profile or {})
    return {
        "verdict": v.verdict,
        "total_known": v.total_known,
        "total_upper": v.total_upper,
        "standard": v.standard,
        "gap": v.gap,
        "direct": list(v.direct or []),
        "notes": list(v.notes or []),
        "items": [
            {"label": i.label, "state": i.state, "points": i.points, "page": i.page}
            for i in v.items
        ],
    }


def dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)
