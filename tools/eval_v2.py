"""评测 v2：题目来自官方「政策问答」（不是我们出的），金标是文号。

与 v1 的区别：
- 金标判"文号"，不判标题关键词（v1 因此把失效版本也算命中）
- version 类额外要求首条是现行有效
- 分 dev / test / holdout 三段，holdout 只在发版时碰
- 顺手报"泄漏度"：题目里有多少词直接出现在金标文件标题里（v1 的 literal 是 100%）

用法：python -m tools.eval_v2 --split dev [--write]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from pathlib import Path

from app.retrieval.index import tokenize
from app.retrieval.search import search

QS = Path("eval/questions_v2.json")
REPORTS = Path("eval/reports")


def norm_title(t: str) -> str:
    t = re.sub(r"[\s　《》〔〕（）()、，,。：:·—\-“”\"']", "", t or "")
    i = t.find("关于")
    return t[i:] if i > 0 else t


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def is_hit(h, gold_doc: str, gold_title: str) -> bool:
    if h.clause.doc_no == gold_doc:
        return True
    return norm_title(h.clause.title) == norm_title(gold_title)


def leakage(q: str, title: str) -> float:
    qt = set(tokenize(q))
    if not qt:
        return 0.0
    return len(qt & set(tokenize(title))) / len(qt)


def run(split: str) -> dict:
    qs = [q for q in json.loads(QS.read_text("utf-8")) if split == "all" or q["split"] == split]
    rows = []
    for item in qs:
        hits = search(item["q"], k=10)
        rank = 0
        for i, h in enumerate(hits, 1):
            if is_hit(h, item["gold_doc"], item["gold_title"]):
                rank = i
                break
        top = hits[0] if hits else None
        rows.append({
            "id": item["id"], "q": item["q"], "cat": item["category"], "split": item["split"],
            "gold": item["gold_doc"], "rank": rank,
            "ok1": int(bool(top) and rank == 1), "ok3": int(1 <= rank <= 3),
            "mrr": 1 / rank if rank else 0.0,
            "answered": int(bool(top)),
            "top": top.clause.doc_no if top else "",
            "top_status": top.clause.status if top else "",
            "leak": round(leakage(item["q"], item["gold_title"]), 2) if item["gold_title"] else 0.0,
        })
    return {"split": split, "rows": rows}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for cat in ("official", "version", "out_of_scope"):
        rs = [r for r in rows if r["cat"] == cat]
        n = len(rs)
        if not n:
            continue
        if cat == "out_of_scope":
            ok = sum(1 for r in rs if not r["answered"])
            lo, hi = wilson(ok, n)
            out[cat] = {"n": n, "refused": ok, "rate": ok / n, "ci": [lo, hi],
                        "wrong": sum(1 for r in rs if r["answered"])}
            continue
        h1 = sum(r["ok1"] for r in rs); h3 = sum(r["ok3"] for r in rs)
        ans = [r for r in rs if r["answered"]]
        l1, u1 = wilson(h1, n)
        d = {"n": n, "hit1": h1 / n, "ci": [l1, u1], "hit3": h3 / n,
             "mrr": sum(r["mrr"] for r in rs) / n,
             "answered": len(ans) / n,
             "hit1_answered": (sum(r["ok1"] for r in ans) / len(ans)) if ans else 0.0,
             "leak_mean": sum(r["leak"] for r in rs) / n}
        if cat == "version":
            d["effective1"] = sum(1 for r in rs if r["ok1"] and r["top_status"] == "effective") / n
        out[cat] = d
    return out


def report(res: dict) -> str:
    s = summarize(res["rows"])
    L = [f"# 评测 v2（{res['split']}，{len(res['rows'])} 条，题目来自官方政策问答）", "",
         "| 类别 | n | hit@1 (95%CI) | hit@3 | MRR | 应答率 | 已答中的 hit@1 | 泄漏度 |",
         "|---|---|---|---|---|---|---|---|"]
    for cat in ("official", "version"):
        b = s.get(cat)
        if not b:
            continue
        lo, hi = b["ci"]
        L.append(f"| {cat} | {b['n']} | {b['hit1']:.0%} ({lo:.0%}–{hi:.0%}) | {b['hit3']:.0%} | "
                 f"{b['mrr']:.2f} | {b['answered']:.0%} | {b['hit1_answered']:.0%} | {b['leak_mean']:.0%} |")
    b = s.get("out_of_scope")
    if b:
        lo, hi = b["ci"]
        L.append(f"| out_of_scope | {b['n']} | — | — | — | 拒答 {b['rate']:.0%} ({lo:.0%}–{hi:.0%}) | — | — |")
    miss = [r for r in res["rows"] if r["cat"] != "out_of_scope" and r["rank"] == 0]
    wrong = [r for r in res["rows"] if r["cat"] == "out_of_scope" and r["answered"]]
    L += ["", f"## 完全没命中（{len(miss)}/{len(res['rows']) - len([r for r in res['rows'] if r['cat']=='out_of_scope'])}）", ""]
    L += [f"- {r['q'][:34]} → {r['top'] or '拒答'}" for r in miss[:25]]
    if wrong:
        L += ["", f"## 该拒答却给了条款（{len(wrong)}）", ""] + [f"- {r['q']} → {r['top']}" for r in wrong]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=("dev", "test", "holdout", "all"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    res = run(a.split)
    md = report(res)
    print(md)
    print(f"\n{len(res['rows'])} 条 / {time.time()-t0:.1f}s")
    if a.write:
        REPORTS.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
        except OSError:
            commit = "?"
        (REPORTS / f"{ts}-v2-{a.split}.md").write_text(md, "utf-8")
        (REPORTS / f"{ts}-v2-{a.split}.json").write_text(
            json.dumps({"meta": {"ts": ts, "commit": commit, "split": a.split},
                        "summary": summarize(res["rows"]), "rows": res["rows"]},
                       ensure_ascii=False, indent=1), "utf-8")
        print(f"→ eval/reports/{ts}-v2-{a.split}.md")


if __name__ == "__main__":
    main()
