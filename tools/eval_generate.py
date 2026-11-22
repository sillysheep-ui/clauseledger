"""生成层评测：给定"大模型看到的条款"，它答出了多少官方答案里的事实。

链路：检索 → 装配上下文 → DeepSeek 生成（每句带 [E#]）→ 机械核验 → 数事实覆盖。

指标：
  fact_cov    官方答案里的「数字+单位」事实，有多少出现在生成文本里
  sent_kept   生成句子中通过核验（有引用、编号存在、数字有出处）的比例
  num_bad     因数字在证据里找不到而被丢掉的句子数
  ctx_any     上下文里至少含一句官方答案的题占比（与 eval_context 同口径，用来对齐）

用法：
  python -m tools.eval_generate --limit 40 --write
  python -m tools.eval_generate --no-llm      # 只跑上下文，不调用模型（基线）
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from app.answer import context as ctx
from app.answer import generate as gen
from app.answer import verify
from app.retrieval import search as S
from tools.eval_answer import covered_sentences, facts

QS = "eval/questions_v3.json"
ANSWERS = "eval/official_answers.json"
OUT = Path("data/reports/generate-eval.json")
NUM = re.compile(r"\d+(?:\.\d+)?")


def load_questions() -> list[dict]:
    qs = json.load(open(QS, encoding="utf-8"))
    return [q for q in qs
            if q.get("category") == "official"
            and q.get("split") == "dev"
            and not q.get("ambiguous") and not q.get("stale")]


def run(k: int, limit: int | None, use_llm: bool) -> dict:
    qs = load_questions()
    if limit:
        qs = qs[:limit]
    answers = json.load(open(ANSWERS, encoding="utf-8"))
    rows = []
    t0 = time.time()
    for q in qs:
        text = q.get("q_ctx") or q["q"]
        official = answers.get(q["id"], "")
        a = S.search_result(text, k=k)
        ev = ctx.assemble(text, a.hits) if a.hits else []
        blob = "".join(e.text for e in ev)
        ctx_any = covered_sentences(official, blob)[0] > 0 if official else False
        row = {"id": q["id"], "kind": a.kind, "ctx_any": ctx_any,
               "n_ev": len(ev), "chars": len(blob)}
        if use_llm and ev and gen.available():
            d = gen.generate(text, ev)
            if d is None:
                row.update({"llm": False})
                rows.append(row)
                continue
            chk = verify.check(d, ev)
            out_text = "".join(str(s.get("text", "")) for s in chk.kept)
            fs = facts(official) if official else []
            hit = sum(1 for f in fs
                      if re.search(re.escape(NUM.search(f).group()) if NUM.search(f) else re.escape(f),
                                   out_text))
            row.update({"llm": True, "n_sent": len(d.sentences), "kept": len(chk.kept),
                        "num_bad": sum(1 for i in chk.issues if "数字" in i),
                        "facts": len(fs), "facts_hit": hit,
                        "usage": d.usage})
        rows.append(row)
    return {"k": k, "n": len(rows), "seconds": round(time.time() - t0, 1), "rows": rows}


def report(o: dict) -> None:
    rows = o["rows"]
    n = len(rows)
    ctx_any = sum(r["ctx_any"] for r in rows)
    print(f"\n== 生成层 k={o['k']}  n={n}  {o['seconds']}s ==")
    print(f"  上下文含答案句 {ctx_any}/{n} = {ctx_any / n:.1%}")
    llm = [r for r in rows if r.get("llm")]
    if not llm:
        print("  （没有调用模型）")
        return
    sent = sum(r["n_sent"] for r in llm)
    kept = sum(r["kept"] for r in llm)
    fs = sum(r["facts"] for r in llm)
    fh = sum(r["facts_hit"] for r in llm)
    bad = sum(r["num_bad"] for r in llm)
    print(f"  句子核验通过 {kept}/{sent} = {kept / max(1, sent):.1%}；数字无出处被丢 {bad} 句")
    print(f"  官方事实覆盖 {fh}/{fs} = {fh / max(1, fs):.1%}（{len(llm)} 题调用成功）")
    # 条件口径：只有上下文里真的有答案句，才谈得上"模型会不会用"。
    sub = [r for r in llm if r.get("ctx_any")]
    if sub:
        sfs = sum(r["facts"] for r in sub); sfh = sum(r["facts_hit"] for r in sub)
        ssent = sum(r["n_sent"] for r in sub); skept = sum(r["kept"] for r in sub)
        print(f"  其中「上下文含答案」的 {len(sub)} 题：事实覆盖 {sfh}/{sfs} = {sfh / max(1, sfs):.1%}"
              f"；句子通过 {skept}/{ssent} = {skept / max(1, ssent):.1%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    o = run(a.k, a.limit, not a.no_llm)
    report(o)
    if a.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
