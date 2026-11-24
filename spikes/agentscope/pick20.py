"""固定 20 题（dev official 前 20 条），写出 questions20.json。"""
import json
from pathlib import Path

qs = json.load(open("eval/questions_v3.json", encoding="utf-8"))
dev = [q for q in qs if q.get("category") == "official" and q.get("split") == "dev"
       and not q.get("ambiguous") and not q.get("stale")]
pick = [{"id": q["id"], "q": q["q"], "gold_doc": q.get("gold_doc")} for q in dev[:20]]
Path("spikes/agentscope/questions20.json").write_text(
    json.dumps(pick, ensure_ascii=False, indent=2), "utf-8")
print(len(pick), "questions -> spikes/agentscope/questions20.json")
