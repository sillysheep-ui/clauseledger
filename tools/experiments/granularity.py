"""粒度扫描：同一批题、同一份语料，只换"检索单元"，看排序差多少。

    python -m tools.experiments.granularity

这是**干预实验**，不是观察归因：把切分这一层换掉，看结果变不变。
四个粒度：
    doc      整份文件当一个块（没有切分）
    section  "一、"级（父条款）
    clause   现在用的条款级
    block    解析出来的原始块（网页段落 / PDF 行）

如果 doc/block 粒度明显更好，说明**切分在拖累排序**；
如果四个粒度差不多，说明问题在词表（BM25 看不见同义）而不是切分边界。
"""
from __future__ import annotations

import json
from pathlib import Path

import bm25s

from app.retrieval.search import tokenize

PROCESSED = Path("data/processed")
QS = Path("eval/questions_v2.json")


def load() -> tuple[dict[str, list[tuple[str, str]]], list[dict]]:
    """返回 {粒度: [(doc_no, text)]} 与题目列表。"""
    levels: dict[str, list[tuple[str, str]]] = {
        "doc": [], "section": [], "clause": [], "block": []}
    for p in PROCESSED.glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        doc_no = d.get("doc_no")
        if not doc_no:
            continue
        body = "\n".join(b.get("text", "") for b in d.get("blocks") or [])
        if body.strip():
            levels["doc"].append((doc_no, body))
        for c in d.get("clauses") or []:
            if c.get("level", 9) <= 3:          # 第X条 / 一、 / （一）
                levels["section"].append((doc_no, c.get("text", "")))
            levels["clause"].append((doc_no, c.get("text", "")))
        for b in d.get("blocks") or []:
            if b.get("text", "").strip():
                levels["block"].append((doc_no, b["text"]))

    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q.get("split") == "dev" and q["category"] == "official"
          and not q.get("ambiguous") and not q.get("stale")]
    return levels, qs


def eval_level(pairs: list[tuple[str, str]], qs: list[dict], k: int = 10) -> dict:
    corpus = [tokenize(t) for _, t in pairs]
    retriever = bm25s.BM25()
    retriever.index(corpus, show_progress=False)
    docs = [d for d, _ in pairs]

    hit1 = hit3 = 0
    ranks: list[int] = []
    for q in qs:
        idx, _ = retriever.retrieve([tokenize(q["q"])], k=k, show_progress=False)
        order = [docs[i] for i in idx[0]]
        r = next((i for i, d in enumerate(order, 1) if d == q["gold_doc"]), 0)
        ranks.append(r)
        hit1 += 1 if r == 1 else 0
        hit3 += 1 if 1 <= r <= 3 else 0
    n = len(qs)
    return {"n": n, "chunks": len(pairs), "hit1": hit1 / n, "hit3": hit3 / n,
            "found": sum(1 for r in ranks if r) / n,
            "mean_rank": sum(r for r in ranks if r) / max(1, sum(1 for r in ranks if r))}


def main() -> int:
    levels, qs = load()
    print(f"dev official n={len(qs)}\n")
    print(f"{'粒度':<9}{'块数':>8}{'hit@1':>9}{'hit@3':>9}{'进前十':>9}{'平均名次':>9}")
    out = {}
    for name in ("doc", "section", "clause", "block"):
        r = eval_level(levels[name], qs)
        out[name] = r
        print(f"{name:<9}{r['chunks']:>8}{r['hit1']:>9.1%}{r['hit3']:>9.1%}"
              f"{r['found']:>9.1%}{r['mean_rank']:>9.1f}")
    best = max(out, key=lambda k: out[k]["found"])
    worst = min(out, key=lambda k: out[k]["found"])
    print(f"\n进前十最好的是 {best}（{out[best]['found']:.1%}），最差的是 {worst}"
          f"（{out[worst]['found']:.1%}）→ 差值 {out[best]['found']-out[worst]['found']:+.1%}")
    print("解读：差值大 = 切分粒度在决定召回；差值小 = 问题在词表/排序，不在边界。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
