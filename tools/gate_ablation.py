"""准出判据的单参数消融：每个常数动一下，看它在 dev / 负样本上到底改变了什么。

做法：先把每题检索一次、把 (top1 分, 分差, 余弦, top1 文号) 记下来，判据本身是
这三项的纯函数，于是所有参数组合都能离线重放，不需要重复检索。

用法：python -m tools.gate_ablation
"""
from __future__ import annotations

import json
from pathlib import Path

from app.retrieval import confidence as C
from app.retrieval import search as S

DEV = Path("eval/questions_v3.json")
MISS = Path("eval/questions_missing.json")


def questions() -> tuple[list[dict], list[dict], list[dict]]:
    qs = json.loads(DEV.read_text("utf-8"))
    dev = [q for q in qs if q.get("category") == "official" and q.get("split") == "dev"
           and not q.get("ambiguous") and not q.get("stale")]
    oos = [q for q in qs if q.get("category") == "out_of_scope"][:18]
    miss = json.loads(MISS.read_text("utf-8"))
    return dev, miss, oos


def record(group: list[dict]) -> list[dict]:
    """跑一遍真实检索，记下判据需要的三个量。"""
    rows = []
    orig = S.decide

    def spy(p, out, **kw):
        ans = orig(p, out, **kw)
        cos = C.cosine(p.query, out[0].child or out[0].clause.text) if out else None
        shown = S._prefer_effective(S._collapse(out)) if out else []
        rows.append({
            "q": p.query,
            "doc": shown[0].clause.doc_no if shown else None,
            "score": out[0].score if out else 0.0,
            "margin": (out[0].score - out[1].score) if len(out) > 1 else (out[0].score if out else 0.0),
            "cos": cos,
            "guarded": ans.outcome not in ("answer", "no_direct_clause"),  # 被前置闸拦掉的
        })
        return ans

    S.decide = spy
    try:
        for q in group:
            S.search_result(q.get("q_ctx") or q["q"], k=5)
    finally:
        S.decide = orig
    return rows


def replay(rows: list[dict], *, w=(C.W_LEX, C.W_MARGIN, C.W_COS), t=0.38) -> list[bool]:
    """按给定权重与阈值重放"答/不答"。"""
    old = (C.W_LEX, C.W_MARGIN, C.W_COS)
    C.W_LEX, C.W_MARGIN, C.W_COS = w
    try:
        return [not r["guarded"] and
                (r["cos"] is not None and C.score(r["score"], r["margin"], r["cos"]) >= t)
                for r in rows]
    finally:
        C.W_LEX, C.W_MARGIN, C.W_COS = old


def stats(rows, ans, golds) -> str:
    n = len(rows)
    answered = sum(ans)
    correct = sum(1 for r, a, g in zip(rows, ans, golds) if a and r["doc"] == g)
    prec = correct / answered * 100 if answered else 0
    return f"{answered:3d}/{n} 答对 {correct:3d} 精度 {prec:3.0f}%"


def main() -> None:
    dev, miss, oos = questions()
    golds = [q.get("gold_doc") for q in dev]
    print("检索中（dev / 负样本）…", flush=True)
    rd, rm, ro = record(dev), record(miss), record(oos)

    cfgs = [
        ("baseline 0.40/0.25/0.35 t=.38", (0.40, 0.25, 0.35), 0.38),
        ("去 lex 项 (0/.33/.67)", (0.0, 0.33, 0.67), 0.38),
        ("去 margin 项 (.53/0/.47)", (0.53, 0.0, 0.47), 0.38),
        ("去 cos 项 (.62/.38/0)", (0.62, 0.38, 0.0), 0.38),
        ("只用词面 (.73/.27/0)", (0.73, 0.27, 0.0), 0.38),
        ("只用余弦 (0/0/1)", (0.0, 0.0, 1.0), 0.38),
    ]
    for name, w, t in cfgs:
        print(f"{name:32s} dev {stats(rd, replay(rd, w=w, t=t), golds)}"
              f"｜无政策误答 {sum(replay(rm, w=w, t=t)):2d}/{len(rm)}"
              f"｜域外误答 {sum(replay(ro, w=w, t=t)):2d}/{len(ro)}")

    print("\n阈值扫描（同一组权重）")
    for t in (0.30, 0.34, 0.38, 0.42, 0.46):
        print(f"t={t:.2f} dev {stats(rd, replay(rd, t=t), golds)}"
              f"｜无政策误答 {sum(replay(rm, t=t)):2d}｜域外误答 {sum(replay(ro, t=t)):2d}")


if __name__ == "__main__":
    main()
