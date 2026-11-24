"""Spike 2 的工具后端：用主项目 venv 跑，输出一行 JSON。

子命令：
    coverage <city>
    search <query> [k]
    clause <doc_no> <hint>
    judge <profile_json>
    draft <run_id> <items_json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def out(obj: dict) -> None:
    print("===JSON===")
    print(json.dumps(obj, ensure_ascii=False))


def cmd_coverage(city: str) -> None:
    from app.domain.coverage import CoverageIndex

    c = CoverageIndex.load().check(city)
    out({"status": c.status, "city": c.city, "message": c.message,
         "allows_retrieval": c.allows_retrieval})


def cmd_search(q: str, k: int = 5) -> None:
    from app.retrieval.search import search_result

    a = search_result(q, k=k)
    out({"kind": a.kind, "refused": bool(a.refused),
         "reason": a.reason if a.refused else None, "as_of": a.as_of,
         "n_docs": a.n_docs, "n_candidates": a.n_candidates,
         "hits": [{"eid": f"E{i}", "citation": h.clause.citation,
                   "doc_no": h.clause.doc_no, "status": h.clause.status,
                   "text": h.clause.text[:800]} for i, h in enumerate(a.hits, 1)]})


def cmd_clause(doc_no: str, hint: str) -> None:
    from app.retrieval import search as S

    _ret, clauses, _children = S._load()
    hits = [c for c in clauses
            if c.doc_no == doc_no and (hint in c.path or hint in c.label or hint in c.text)]
    hit_list = [{"citation": c.citation, "doc_no": c.doc_no, "path": c.path,
                 "text": c.text[:1200], "status": c.status} for c in hits[:3]]
    out({"doc_no": doc_no, "hint": hint, "n": len(hits), "hits": hit_list})


def cmd_judge(profile_json: str) -> None:
    from app.judgement.luohu import evaluate

    v = evaluate(json.loads(profile_json))
    out({"verdict": v.verdict, "total_known": v.total_known, "total_upper": v.total_upper,
         "standard": v.standard, "gap": v.gap, "direct": v.direct, "notes": v.notes,
         "items": [{"id": i.id, "label": i.label, "state": i.state,
                    "points": i.points, "page": i.page} for i in v.items]})


def cmd_draft(run_id: str, items_json: str) -> None:
    from app.runtime.actions import apply

    items = json.loads(items_json)
    res = apply(run_id, "todo", {"items": items})
    out({"run_id": run_id, "n_items": len(items), **res})


def cmd_exists(doc_no: str) -> None:
    from app.retrieval import search as S

    _ret, clauses, _children = S._load()
    hit = [c for c in clauses if c.doc_no == doc_no]
    out({"doc_no": doc_no, "exists": bool(hit), "n_clauses": len(hit),
         "title": hit[0].title if hit else None})


def cmd_count(kind: str | None = None) -> None:
    from app.runtime.actions import count

    out({"kind": kind, "count": count(kind)})


def main() -> None:
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "coverage":
        cmd_coverage(*args)
    elif cmd == "search":
        cmd_search(args[0], int(args[1]) if len(args) > 1 else 5)
    elif cmd == "clause":
        cmd_clause(*args)
    elif cmd == "judge":
        cmd_judge(*args)
    elif cmd == "draft":
        cmd_draft(*args)
    elif cmd == "exists":
        cmd_exists(args[0])
    elif cmd == "count":
        cmd_count(args[0] if args else None)
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
