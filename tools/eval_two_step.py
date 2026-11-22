"""两步评测：先建召回池（不看排序），再在同一批池子上看重排。

为什么分两步：
  · 重排不改变"池子里有没有"，只改变"第几名"——和召回混在一起测，两个问题都看不清。
  · 池子存盘后，重排实验可以反复跑、分母永远相同，也能离线复现。

用法：
  python -m tools.eval_two_step --build         # 第一步：建池（rerank 不参与）
  python -m tools.eval_two_step --score         # 第二步：在池上跑重排
  python -m tools.eval_two_step --build --score
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

POOL = Path("eval/pools/dev.json")
QUESTIONS = Path("eval/questions_v3.json")


def _questions() -> list[dict]:
    qs = json.loads(QUESTIONS.read_text("utf-8"))
    out = []
    for q in qs:
        if q.get("category") != "official" or q.get("split") != "dev":
            continue
        if q.get("ambiguous") or q.get("stale"):
            continue
        out.append(q)
    return out


def build(size: int = 50) -> None:
    from app.retrieval.search import pool

    rows = []
    t0 = time.time()
    for q in _questions():
        p = pool(q.get("q_ctx") or q["q"], k=5, pool_size=size)
        cands = []
        for c in p.cands[:size]:
            cl = c.clause
            cands.append({
                "clause_id": cl.id,
                "doc_no": cl.doc_no,
                "title": cl.title,
                "path": list(cl.path),
                "page": cl.page,
                "score": round(float(c.score), 4),
                "text": cl.text[:1200],
            })
        rows.append({
            "id": q["id"],
            "q": q.get("q_ctx") or q["q"],
            "gold_doc": q["gold_doc"],
            "gold_title": q.get("gold_title"),
            "mode": p.mode,
            "cands": cands,
        })
    POOL.parent.mkdir(parents=True, exist_ok=True)
    POOL.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    print(f"· 建池 {len(rows)} 题 / 每题 {size} 条 / {time.time()-t0:.1f}s → {POOL}")


def _stats(rows: list[dict], order_of) -> dict:
    """按给定排序函数算 P@1 / P@3 / MRR（分母 = 传入的题数）。"""
    p1 = p3 = 0
    rr = 0.0
    for r in rows:
        order = order_of(r)
        rank = next((i for i, c in enumerate(order, 1) if c["doc_no"] == r["gold_doc"]), None)
        if rank == 1:
            p1 += 1
        if rank and rank <= 3:
            p3 += 1
        if rank:
            rr += 1.0 / rank
    n = len(rows)
    return {"n": n, "p@1": p1 / n, "p@3": p3 / n, "mrr": rr / n}


def score(top: int = 40) -> None:
    from app.retrieval import rerank

    rows = json.loads(POOL.read_text("utf-8"))
    # 池内才有意义：金标不在池子里的题，重排最多只能"换个错的"。
    inside = [r for r in rows if any(c["doc_no"] == r["gold_doc"] for c in r["cands"])]
    if not inside:
        raise SystemExit("池子里一题金标都没有，先跑 --build")

    def base_order(r):
        return sorted(r["cands"], key=lambda c: -c["score"])

    base = _stats(inside, base_order)

    model = rerank._load()
    got: dict[str, float] = {}
    t0 = time.time()
    for r in inside:
        cands = r["cands"][:top]
        pairs = [(r["q"], c["text"]) for c in cands]
        for c, s in zip(cands, model.predict(pairs, batch_size=16, show_progress_bar=False)):
            got[c["clause_id"]] = float(s)
    dur = time.time() - t0

    def rr_order(r):
        # top 以外的候选没有重排分，排在后面但保持原相对顺序
        head = sorted(r["cands"][:top], key=lambda c: -got.get(c["clause_id"], -1e9))
        return head + r["cands"][top:]

    rr = _stats(inside, rr_order)
    win = sum(1 for r in inside
              if rr_order(r)[0]["doc_no"] == r["gold_doc"] and base_order(r)[0]["doc_no"] != r["gold_doc"])
    lose = sum(1 for r in inside
               if base_order(r)[0]["doc_no"] == r["gold_doc"] and rr_order(r)[0]["doc_no"] != r["gold_doc"])

    print(f"池内题数 {base['n']} / 全池 {len(rows)}；重排 top={top}，{dur:.1f}s（{dur/base['n']:.2f}s/题）")
    print(f"  基础序：P@1 {base['p@1']:.1%}  P@3 {base['p@3']:.1%}  MRR {base['mrr']:.3f}")
    print(f"  重排后：P@1 {rr['p@1']:.1%}  P@3 {rr['p@3']:.1%}  MRR {rr['mrr']:.3f}")
    print(f"  配对：改好 {win} / 改坏 {lose}（净 {win-lose:+d}）")

    out = Path("eval/reports") / f"two-step-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"base": base, "rerank": rr, "win": win, "lose": lose,
                               "n_pool": len(rows), "top": top, "seconds": round(dur, 1)},
                              ensure_ascii=False, indent=1), "utf-8")
    print(f"· 明细 → {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()
    if a.build or not a.score:
        build(a.size)
    if a.score:
        score(a.top)


if __name__ == "__main__":
    main()
