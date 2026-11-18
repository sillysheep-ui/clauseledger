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
import os
import math
import re
import subprocess
import time
from pathlib import Path

from app.retrieval.index import tokenize
from app.retrieval.search import search, search_result, MIN_ANSWER, MIN_COVERAGE

QS = Path(os.environ.get("CLAUSELEDGER_QUESTIONS", "eval/questions_v2.json"))
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


# top-1 条款文本里的"不该出现的字"：页尾、导航、被注释掉的 JS、没剥干净的标签。
# 它不影响 hit@1（金标只判文号），但决定"引用出去的那段话干不干净"。
ARTIFACT = ("const ", "function(", "encodeURIComponent", "document.writeln",
            "<!--", "<div", "<span", "您现在的位置", "当前位置",
            "相关稿件", "分享到", "打印本页")


def dirty(text: str) -> int:
    return int(any(a in (text or "") for a in ARTIFACT))


def run(split: str) -> dict:
    allq = [q for q in json.loads(QS.read_text("utf-8")) if split == "all" or q["split"] == split]
    qs = [q for q in allq if not q.get("ambiguous") and not q.get("stale")]
    n_amb = sum(1 for q in allq if q.get("ambiguous"))
    n_stale = sum(1 for q in allq if q.get("stale"))
    rows = []
    for item in qs:
        ans = search_result(item["q"], k=10)
        # no_direct_clause 也带回候选：不当作"答了"，但金标是否在候选里要单独看
        hits = ans.hits if ans.kind in ("ok", "no_direct_clause") else []
        conf = ans.confidence if ans.kind in ("ok", "no_direct_clause") else "none"
        rank = 0
        for i, h in enumerate(hits, 1):
            if is_hit(h, item["gold_doc"], item["gold_title"]):
                rank = i
                break
        top = hits[0] if hits else None
        rows.append({
            "id": item["id"], "q": item["q"], "cat": item["category"], "split": item["split"],
            "gold": item["gold_doc"], "rank": rank,
            "ok1": int(ans.kind == "ok" and rank == 1),
            "ok3": int(ans.kind == "ok" and 1 <= rank <= 3),
            "cand1": int(ans.kind == "no_direct_clause" and rank == 1),
            "cand3": int(ans.kind == "no_direct_clause" and 1 <= rank <= 3),
            "mrr": 1 / rank if rank else 0.0,
            "answered": int(ans.kind == "ok"),
            "cand": int(ans.kind == "no_direct_clause"),
            "top": top.clause.doc_no if top else "",
            "top_status": top.clause.status if top else "",
            "conf": conf,
            "dirty": dirty(top.clause.text) if top else 0,
            "leak": round(leakage(item["q"], item["gold_title"]), 2) if item["gold_title"] else 0.0,
        })
    return {"split": split, "rows": rows, "ambiguous": n_amb, "stale": n_stale}


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
             "clean": (1 - sum(r["dirty"] for r in ans) / len(ans)) if ans else 0.0,
             "leak_mean": sum(r["leak"] for r in rs) / n}
        if cat == "version":
            d["effective1"] = sum(1 for r in rs if r["ok1"] and r["top_status"] == "effective") / n
        out[cat] = d
    return out


def report(res: dict) -> str:
    s = summarize(res["rows"])
    L = [f"# 评测 v2（{res['split']}，{len(res['rows'])} 条，题目来自官方政策问答）", "",
         "| 类别 | n | hit@1 (95%CI) | hit@3 | MRR | 应答率 | 已答中的 hit@1 | 泄漏度 | 干净率 |",
         "|---|---|---|---|---|---|---|---|---|"]
    if res.get("ambiguous"):
        L.append("")
        L.append(f"> 另有 **{res['ambiguous']} 条标记 `ambiguous`**（官方问答脱离文章上下文，题干缺主语），"
                 "不计入分母；用例保留可复核。")
        L.append("")
    for cat in ("official", "version"):
        b = s.get(cat)
        if not b:
            continue
        lo, hi = b["ci"]
        L.append(f"| {cat} | {b['n']} | {b['hit1']:.0%} ({lo:.0%}–{hi:.0%}) | {b['hit3']:.0%} | "
                 f"{b['mrr']:.2f} | {b['answered']:.0%} | {b['hit1_answered']:.0%} | {b['leak_mean']:.0%} | "
                 f"{b.get('clean', 0):.0%} |")
    b = s.get("out_of_scope")
    if b:
        lo, hi = b["ci"]
        L.append(f"| out_of_scope | {b['n']} | — | — | — | 拒答 {b['rate']:.0%} ({lo:.0%}–{hi:.0%}) | — | — |")
    # 低置信：答了但首条只覆盖三四成提问词。它既不是拒答也不是"有把握"，
    # 单独报一行，避免用整体 hit@1 掩盖"多答出来的是错的多"。
    cand = [r for r in res["rows"] if r.get("cand")]
    if cand:
        L.append("")
        L.append(f"> 未下结论（no_direct_clause，只给候选）**{len(cand)} 条**，"
                 f"其中金标就在候选第 1 名 **{sum(r['cand1'] for r in cand)}** 条、"
                 f"前 3 名 **{sum(r['cand3'] for r in cand)}** 条。")
    low = [r for r in res["rows"] if r.get("conf") == "low"]
    if low:
        lh = sum(1 for r in low if r["ok1"])
        L.append("")
        L.append(f"> 低置信（覆盖 {MIN_ANSWER:.2f}–{MIN_COVERAGE:.2f}，答但标「请核对」）**{len(low)} 条**，其中 hit@1 **{lh}** 条；"
                 f"高置信 hit@1 {sum(1 for r in res['rows'] if r.get('conf') == 'high' and r['ok1'])} 条。")
    # 金标自己已经废止的题：整体 hit@1 会被它们拖低。单列一行，不许混着报。
    try:
        from app.domain.validity import load as _load_rels
        _rels = _load_rels()
    except Exception:
        _rels = {}
    if _rels:
        rows = [r for r in res["rows"] if r["cat"] == "official"]
        live = [r for r in rows if r["gold"] not in _rels]
        dead = [r for r in rows if r["gold"] in _rels]
        _top_stale = sum(1 for r in rows if r.get("answered") and r.get("top") in _rels)
        L += ["", f"> 现行性：top-1 引用了**已被废止**的文件 **{_top_stale}/{len(rows)}**"
              "（文号可能对、内容已过期；这是关系层目前只标注不改分的结果）。"]
        if live:
            lo, hi = wilson(sum(1 for r in live if r["ok1"]), len(live))
            L += ["", f"> 金标仍有效子集：hit@1 **{sum(1 for r in live if r['ok1'])}/{len(live)} = "
                  f"{sum(1 for r in live if r['ok1'])/len(live):.0%}**（{lo:.0%}–{hi:.0%}）；"
                  f"另有 **{len(dead)} 条金标已被语料内文件废止**，仍计入上表。"
                  "两个数都要报：前者是能力，后者是可比的旧口径。"]
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
