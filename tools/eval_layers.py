"""分层评测：召回 / 排序 / 决策，各算各的。

为什么要把它们拆开：

* `hit@1` 是端到端指标，它同时包含"候选池里有没有"（召回）、"排第几"（排序）、
  "敢不敢答"（决策）三件事。任何一层改动都会让这个数动，于是每次改动都只能
  说"在噪声内"——这不是统计功效不够，是**指标把三种不同的失败混成了一个数**。

分层口径（分母必须写清）：

L1 召回   R@k = 金标出现在候选前 k 名的题数 / 全部题数
          —— 与阈值、闸门、加分全部无关：把阈值放到 0 取候选。
L2 排序   P@1|R@k = 金标在候选前 k 名内、且排第 1 的题数 / 金标在候选前 k 名内的题数
          —— 只在"捞到了"的题里看排得对不对。
L3 决策   作答率 = 下了结论的题数 / 全部题数
          作答精度 = 作答且命中的题数 / 作答题数
          负样本（域外 / 域内无政策）只报"正确拒答率"，不掺进正样本。
L4 证据   引用可定位率（CLR）—— 由 tools/eval_citation.py 单独跑，本脚本不混。

用法：
    python -m tools.eval_layers                      # 官方 dev + 口语 + 负样本
    python -m tools.eval_layers --split dev+test
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.eval_v2 import is_hit, same_family, wilson

QUESTIONS = Path("eval/questions_v3.json")
PARAPHRASE = Path("eval/questions_paraphrase.json")
MISSING = Path("eval/questions_missing.json")

# 放开所有阈值：这一层只问"候选里有没有"，不问"敢不敢答"
RELAX = dict(min_score=0.0, min_cand_cov=0.0, min_coverage=0.0,
             min_answer=-1.0, min_score_strong=-1.0, min_margin_strong=-1.0)


def load(path: Path, cats: tuple[str, ...], splits: tuple[str, ...]) -> list[dict]:
    rows = json.loads(path.read_text("utf-8"))
    out = []
    for q in rows:
        if cats and q.get("category") not in cats:
            continue
        if q.get("split") and q["split"] not in splits:
            continue
        out.append(q)
    return out


def surface(rows: list[dict], ks=(5, 10, 50)) -> dict:
    """第一遍：放开阈值取候选，算 R@k 与名次。"""
    from app.retrieval.search import search_result

    n = len(rows)
    ranks: list[int | None] = []
    fam = 0
    for q in rows:
        ans = search_result(q["q"], k=max(ks), **RELAX)
        rank = None
        for i, h in enumerate(ans.hits, 1):
            if is_hit(h, q.get("gold_doc"), q.get("gold_title")):
                rank = i
                break
        ranks.append(rank)
        if rank is None and ans.hits and same_family(ans.hits[0], q.get("gold_title") or ""):
            fam += 1

    out = {"n": n, "family_wrong": fam}
    for k in ks:
        got = sum(1 for r in ranks if r and r <= k)
        out[f"R@{k}"] = (got, n)
    for k in (5, 10, 50):
        pool = [r for r in ranks if r and r <= k]
        top1 = sum(1 for r in pool if r == 1)
        out[f"P@1|R@{k}"] = (top1, len(pool))
    return out


def decide(rows: list[dict]) -> dict:
    """第二遍：按生产阈值跑，只算"敢不敢答、答得对不对"。"""
    from app.retrieval.search import search_result

    answered = correct = 0
    for q in rows:
        ans = search_result(q["q"], k=8)
        if ans.kind != "ok":
            continue
        answered += 1
        if ans.hits and is_hit(ans.hits[0], q.get("gold_doc"), q.get("gold_title")):
            correct += 1
    return {"answered": answered, "correct": correct, "n": len(rows)}


def neg_refuse(rows: list[dict]) -> dict:
    """负样本：正确结局是"不回答"。"""
    from app.retrieval.search import search_result

    ok = 0
    bad = []
    for q in rows:
        ans = search_result(q["q"], k=8)
        if ans.kind in ("ok",):
            bad.append(q["q"])
        else:
            ok += 1
    return {"refused": ok, "n": len(rows), "wrong": bad}


def pct(pair: tuple[int, int]) -> str:
    got, n = pair
    if not n:
        return "—"
    lo, hi = wilson(got, n)
    return f"{got}/{n} = {got/n:.0%} ({lo:.0%}–{hi:.0%})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=("dev", "dev+test", "all"))
    a = ap.parse_args()
    splits = ("dev",) if a.split == "dev" else (("dev", "test") if a.split == "dev+test" else ("dev", "test", "holdout"))

    groups = {
        "官方题": load(QUESTIONS, ("official",), splits),
        "口语题": load(PARAPHRASE, (), splits),
        "域内无政策": load(MISSING, (), ("dev",)),
        "域外": load(QUESTIONS, ("out_of_scope",), splits),
    }

    print(f"# 分层评测（split={a.split}）\n")
    for name, rows in groups.items():
        if not rows:
            continue
        print(f"## {name}（n={len(rows)}）")
        if name in ("域内无政策", "域外"):
            r = neg_refuse(rows)
            print(f"- 决策：正确不回答 {pct((r['refused'], r['n']))}")
            if r["wrong"]:
                print(f"- 误答 {len(r['wrong'])} 条：" + "；".join(r["wrong"][:3]))
            print()
            continue
        s = surface(rows)
        d = decide(rows)
        print(f"- L1 召回：R@5 {pct(s['R@5'])}｜R@10 {pct(s['R@10'])}｜R@50 {pct(s['R@50'])}")
        print(f"- L2 排序：P@1|R@10 {pct(s['P@1|R@10'])}｜P@1|R@50 {pct(s['P@1|R@50'])}")
        print(f"- 同族错版（标题同、文号不同）作 top-1：{s['family_wrong']} 题")
        print(f"- L3 决策：作答 {d['answered']}/{d['n']} = {d['answered']/d['n']:.0%}"
              f"｜作答精度 {d['correct']}/{max(1,d['answered'])} = {d['correct']/max(1,d['answered']):.0%}")
        print()


if __name__ == "__main__":
    main()
