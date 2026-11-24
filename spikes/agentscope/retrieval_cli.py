"""供 AgentScope spike 调用：把检索结果输出成一行 JSON。

用法（必须用主项目的 venv 跑，因为要 import app.*）：
    .venv/bin/python spikes/agentscope/retrieval_cli.py "失业保险金能领多久" 5
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.retrieval.search import search_result  # noqa: E402


def main() -> None:
    q = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    a = search_result(q, k=k)
    out = {
        "kind": a.kind,
        "refused": bool(a.refused),
        "reason": a.reason if a.refused else None,
        "as_of": a.as_of,
        "n_docs": a.n_docs,
        "n_candidates": a.n_candidates,
        "hits": [
            {
                "eid": f"E{i}",
                "citation": h.clause.citation,
                "doc_no": h.clause.doc_no,
                "page": h.clause.page,
                "status": h.clause.status,
                "text": h.clause.text[:800],
            }
            for i, h in enumerate(a.hits, 1)
        ],
    }
    print("===JSON===")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
