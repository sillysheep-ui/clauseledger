"""端到端漏斗评测：从一句提问，到用户手里那张卡，每一步剩多少。

为什么不用一个 hit@1 说完：一条链路有五个会掉人的地方，任何一处掉，
用户都拿不到答案。只报一个数，说不清是"没找到"还是"找到了不敢说"。

漏斗（每一步都写清分母）：

S1 覆盖闸   题被放进检索（不是 not_covered / out_of_domain 提前拒答）
S2 召回      金标文件在候选前 10 名（放开阈值取候选）
S3 排序      金标文件排第 1
S4 取证      卡里的证据引用了金标文件（用户看得到它）
S5 内容      卡里的引文覆盖了官方答案（至少一句 / 过半）
S6 决策      系统真的作答了（不是拒答/不下结论）
S7 端到端    作答 + 引对文件 + 覆盖官方答案

用法：
    python -m tools.eval_funnel                 # dev 官方题
    python -m tools.eval_funnel --limit 5       # 先看几条
    python -m tools.eval_funnel --paraphrase    # 口语题
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import tempfile
from pathlib import Path

from tools.eval_answer import covered_sentences, gold_text
from tools.eval_v2 import wilson

RELAX = dict(min_score=0.0, min_cand_cov=0.0, min_coverage=0.0,
             min_answer=-1.0, min_score_strong=-1.0, min_margin_strong=-1.0)


def qa_index() -> dict[str, dict[str, str]]:
    """source URL -> {问题原文: 官方答案}"""
    idx: dict[str, dict[str, str]] = {}
    for f in glob.glob("data/harvest/qa/*.json"):
        d = json.loads(Path(f).read_text("utf-8"))
        for item in d.get("qa") or []:
            q = (item.get("q") or item.get("question") or "").strip()
            a = (item.get("a") or item.get("answer") or "").strip()
            if q:
                idx.setdefault(d["url"], {})[q] = a
    return idx


def pct(k: int, n: int) -> str:
    if not n:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k/n:.0%} ({lo:.0%}–{hi:.0%})"


def card_text(card: dict) -> str:
    parts = [card.get("headline") or ""]
    for e in card.get("evidence") or []:
        parts.append(e.get("quote") or "")
        parts.append(e.get("text") or "")
    for it in card.get("items") or []:
        parts.append(it.get("quote") or "")
    return "\n".join(p for p in parts if p)


def card_docs(card: dict) -> set[str]:
    out = set()
    for e in card.get("evidence") or []:
        if e.get("doc_no"):
            out.add(e["doc_no"])
    for it in card.get("items") or []:
        if it.get("doc_no"):
            out.add(it["doc_no"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--paraphrase", action="store_true")
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()

    path = "eval/questions_paraphrase.json" if a.paraphrase else "eval/questions_v3.json"
    rows = json.loads(Path(path).read_text("utf-8"))
    if not a.paraphrase:
        rows = [q for q in rows if q.get("category") == "official" and q.get("split") == a.split]
    if a.limit:
        rows = rows[: a.limit]

    os.environ["CLAUSELEDGER_RUNS"] = tempfile.mkdtemp(prefix="funnel-")
    from app.retrieval.search import search_result
    from app.runtime.engine import Engine

    eng = Engine()
    qa = qa_index()

    s1 = s2 = s3 = s4 = s5a = s5b = s6 = s7a = s7b = 0
    n5 = 0            # 有官方答案可核对的题数
    rows_out = []

    for q in rows:
        if q.get("ambiguous"):
            continue
        text = q.get("q_ctx") or q["q"]
        st = eng.run(text)
        ans = (st.answer or {}).get("card") or {}
        ans_kind = (st.answer or {}).get("kind") or ""
        kind = ans.get("kind") or "refusal"
        txt = card_text(ans)
        docs = card_docs(ans)

        gate_ok = bool((st.coverage or {}).get("allows_retrieval", True))
        a2 = search_result(text, k=10, **RELAX)
        rank = None
        for i, h in enumerate(a2.hits, 1):
            if h.clause.doc_no == q["gold_doc"]:
                rank = i
                break
        r10 = rank is not None
        r1 = rank == 1
        ev_gold = q["gold_doc"] in docs
        answered = ans_kind == "ok"

        official = (qa.get(q.get("source") or "") or {}).get(q["q"].strip(), "")
        cov = None
        traceable = False
        if official:
            # 只有"官方答案确实写在这份金标文件里"的题，才能用逐字覆盖判分
            traceable = covered_sentences(official, gold_text(q["gold_doc"]))[0] > 0
            if traceable:
                cov = covered_sentences(official, txt)
                n5 += 1
        c_any = bool(cov and cov[0] >= 1)
        c_half = bool(cov and cov[0] * 2 >= max(1, cov[1]))

        s1 += gate_ok
        s2 += r10
        s3 += r1
        s4 += ev_gold
        s6 += answered
        if traceable:
            s5a += c_any
            s5b += c_half
        ok_a = answered and ev_gold and (c_any if traceable else r1)
        ok_b = answered and ev_gold and (c_half if traceable else r1)
        s7a += ok_a
        s7b += ok_b
        rows_out.append(dict(id=q.get("id"), q=q["q"], rank=rank, kind=ans_kind, trace=traceable,
                             ev_gold=ev_gold, cov=cov, ok_a=ok_a, ok_b=ok_b))

    n = sum(1 for q in rows if not q.get("ambiguous"))
    tag = "口语" if a.paraphrase else f"官方 {a.split}"
    print(f"# 端到端漏斗（{tag}，n={n}）\n")
    print(f"- S1 覆盖闸放行：{pct(s1, n)}")
    print(f"- S2 召回 R@10：{pct(s2, n)}")
    print(f"- S3 排序 第 1：{pct(s3, n)}")
    print(f"- S4 卡里引对文件：{pct(s4, n)}")
    print(f"- S5 内容覆盖（至少一句）：{pct(s5a, n5)}｜过半句子：{pct(s5b, n5)}  （只在「官方答案确实写在该文件里」的 n={n5} 题上算）")
    print(f"- S6 真的作答：{pct(s6, n)}")
    print(f"- S7 端到端（至少一句）：{pct(s7a, n)}｜（过半句子）：{pct(s7b, n)}")
    print()
    print("失败样本前 10 条：")
    for r in [x for x in rows_out if not x["ok_b"]][:10]:
        print(f"  {r['id']} rank={r['rank']} kind={r['kind']} ev={r['ev_gold']} cov={r['cov']} {r['q'][:30]}")


if __name__ == "__main__":
    main()
