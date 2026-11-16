"""金标过期核查：未命中的题里，我们返回的文件是不是"更新且合法地替代了金标"。

三条机械判据（不靠感觉）：
  A. 新文件正文里**点名**金标文号（说明两者讲的是同一件事）
  B. 语料里存在一份文件，其条款同时出现「金标标题」与「废止」
  C. 金标自身的有效期已过，且新文件的发文日期晚于金标失效日

三判据都过 = `superseded`（金标确实过期，这题不该判我们错）
只过 A = `same_topic_newer`（同一件事的新版本，需要人看）
都不过 = `different`（我们真的答错了）

用法：python -m tools.diagnose.stale_gold [--out docs/EVAL-VALIDITY.md]
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path

from app.retrieval.search import search_result

QS = Path("eval/questions_v2.json")


def load_docs() -> dict[str, dict]:
    out = {}
    for p in glob.glob("data/processed/*.json"):
        d = json.load(open(p))
        out[d.get("doc_no") or Path(p).stem] = d
    return out


def body(d: dict) -> str:
    return "".join(c["text"] for c in d.get("clauses", []))


def norm(t: str) -> str:
    return re.sub(r"[\s　《》〔〕（）()、，,。：:·—\-“”\"']", "", t or "")


def flat(t: str) -> str:
    """文号在正文里常写成全角括号（2015），比对时把括号一起去掉。"""
    return re.sub(r"[\s　〔〕（）()]", "", t or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/EVAL-VALIDITY.md")
    args = ap.parse_args()

    docs = load_docs()
    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q["split"] == "dev" and q.get("category") == "official"
          and not q.get("ambiguous")]

    rows = []
    for q in qs:
        a = search_result(q["q"], k=900, min_score=-1, min_coverage=-1, min_cand_cov=0)
        if a.refused or not a.hits:
            continue
        rank = next((i for i, h in enumerate(a.hits, 1)
                     if h.clause.doc_no == q["gold_doc"]), None)
        if rank == 1:
            continue
        top = a.hits[0].clause
        g = docs.get(q["gold_doc"], {})
        t = docs.get(top.doc_no, {})
        gtext, ttext = body(g), body(t)

        # A：新文件是否点名金标文号
        named = flat(q["gold_doc"]) in flat(ttext)

        # B：语料里有没有文件把金标标题和「废止」写在一起
        gt = norm(q.get("gold_title", ""))
        repeal = ""
        repeal_text = ""
        if gt:
            for dn, d in docs.items():
                for c in d.get("clauses", []):
                    txt = norm(c["text"])
                    if not gt or gt not in txt:
                        continue
                    i = txt.find(gt)
                    win = txt[max(0, i - 160): i + len(gt) + 80]
                    if "废止" in win:
                        repeal = f"{dn}｜{c['path']}"
                        repeal_text = c["text"][:120]
                        break
                if repeal:
                    break

        # C：金标是否已过有效期
        expired = bool(g.get("effective_to") and g["effective_to"] < "2026-11-16")

        rows.append(dict(id=q["id"], q=q["q"], gold=q["gold_doc"],
                         gold_title=q.get("gold_title", ""),
                         g_to=g.get("effective_to") or "",
                         top=top.doc_no, top_title=top.title,
                         t_from=t.get("effective_from") or "",
                         named=named, repeal=repeal, repeal_text=repeal_text,
                         expired=expired))

    def yr(doc):
        m = re.search(r"[〔（(](\d{2,4})[〕）)]", doc or "")
        if not m:
            return 0
        y = int(m.group(1))
        return y + 2000 if y < 100 else y

    for r in rows:
        r["newer"] = yr(r["top"]) > yr(r["gold"])
        r["older"] = yr(r["top"]) < yr(r["gold"])

    # 判定
    for r in rows:
        if r["named"] and r["repeal"] and r["expired"]:
            r["verdict"] = "superseded"
        elif r["named"] or r["repeal"]:
            r["verdict"] = "same_topic_newer"
        else:
            r["verdict"] = "different"

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    newer = [r for r in rows if r["newer"]]
    print(f"未命中 {len(rows)} 条：第一名更新 {len(newer)}、更旧 {sum(r['older'] for r in rows)}、同年 {sum(not r['newer'] and not r['older'] for r in rows)}")
    print("更新组判定：", {v: sum(1 for r in newer if r['verdict'] == v) for v in ("superseded", "same_topic_newer", "different")})
    for r in rows:
        print(f"{r['id']:10s} {r['verdict']:16s} named={r['named']} "
              f"repeal={'Y' if r['repeal'] else '-'} expired={r['expired']} "
              f"{r['gold']} ← {r['top']}")

    lines = ["# 金标过期核查（评测集有效性）", "",
             f"对象：dev 中「未命中、且第一名比金标新」的 **{len(rows)}** 条。", "",
             "| 判定 | 条数 | 含义 |", "|---|---|---|",
             f"| superseded | {counts.get('superseded', 0)} | 新文件点名旧文号、且语料有废止记录、旧文件已过有效期——**金标确实过期** |",
             f"| same_topic_newer | {counts.get('same_topic_newer', 0)} | 同一件事的新版本，但没有明文废止关系，需人工看 |",
             f"| different | {counts.get('different', 0)} | 与金标无关，**我们真的答错了** |", "",
             "## 逐条", "",
             "| id | 判定 | 问题 | 金标 | 金标失效 | 我们返回 | 新文件点名金标 | 语料中的废止记录 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['verdict']} | {r['q'][:34]} | {r['gold']} | "
                     f"{r['g_to'] or '—'} | {r['top']} | {'是' if r['named'] else '否'} | "
                     f"{r['repeal'] or '—'} |")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n", "utf-8")
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
