"""检索冒烟测试：12 条真实问题，检查第一条命中文件的**标题**是否切题。

金标用"标题里必须出现的词"而不是文号：同一政策逐年发文（2016/2017/2018 三个
"关于调整失业保险金支付标准的通知"），绑死某一个文号会把正确答案判成错的。
"""
import json
from pathlib import Path

from app.retrieval.search import search

qs = json.loads(Path("eval/questions.json").read_text("utf-8"))
hit1 = hit3 = 0
for item in qs:
    hits = search(item["q"], k=3)
    ok = [any(k in h.clause.title for k in item["expect_title_kw"]) for h in hits]
    ok += [False] * (3 - len(ok))
    hit1 += ok[0]
    hit3 += any(ok)
    mark = "✓" if ok[0] else ("△" if any(ok) else "✗")
    top = hits[0].clause if hits else None
    print(f"{mark} {item['q'][:20]:20} → {(top.title[:26] if top else '无结果')}"
          f"  v={hits[0].versions if hits else 0}")
print(f"\nhit@1 {hit1}/{len(qs)} = {hit1/len(qs):.0%}   hit@3 {hit3}/{len(qs)} = {hit3/len(qs):.0%}")
