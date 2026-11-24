"""用主项目 pipeline 跑同样 20 题，记录 top5 文号与 kind。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.retrieval.search import search_result  # noqa: E402

qs = json.load(open("spikes/agentscope/questions20.json", encoding="utf-8"))
rows = []
for q in qs:
    a = search_result(q["q"], k=5)
    docs = [h.clause.doc_no for h in a.hits]
    rows.append({"id": q["id"], "gold_doc": q["gold_doc"], "kind": a.kind,
                 "docs": docs, "hit5": q["gold_doc"] in docs})
Path("spikes/agentscope/pipeline20.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
print("hit@5:", sum(r["hit5"] for r in rows), "/", len(rows))
