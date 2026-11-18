"""答案级评测：不测"文件排第几"，测"用户看到的卡里有没有官方答案的关键事实"。

为什么这么测：

    hit@1 回答的是"排序对不对"，而产品给用户的是一张卡。
    一张卡可以排错名次却仍然答对（三条候选里有一条够用），
    也可以排对名次却答不全（关键数字在引文之外）。

口径（机械可判定，不打主观分）：

    关键事实 = 官方答案里的"数字 + 单位"（元/万元/%/个月/日/个工作日/岁/分/倍/人）
    覆盖 = 卡里（引文 + 标题）能逐字找到的"数字+单位"占比

局限（必须一起报）：

    - 只核对数字，不核对条件词（"不超过" vs "不低于"）——这一层要人工；
    - 卡只带 3 条引文、每条 240 字，事实可能被截断，这是真实产品限制；
    - 官方答案是问答体，可能包含我们没检索到的补充说明。

用法：

    python -m tools.eval_answer                 # 默认跑全部有官方答案的题
    python -m tools.eval_answer --limit 40      # 抽样
    python -m tools.eval_answer --write         # 落 data/reports/answer-eval.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HARVEST = Path("data/harvest/qa")
QUESTIONS = Path("eval/questions_v3.json")
REPORT = Path("data/reports/answer-eval.md")

BOILER = re.compile(r"(扫一扫在手机打开当前页|打印本页|关闭窗口|相关稿件)+$")
FW = str.maketrans("０１２３４５６７８９．％", "0123456789.%")

FACT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万元|元|%|个月|个工作日|工作日|月|日|天|小时|岁|分|倍|人)")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").translate(FW))


DATE = re.compile(r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|\d{1,2}\s*月\s*\d{1,2}\s*日")


def facts(answer: str, limit: int = 6) -> list[str]:
    """从官方答案里抽"数字+单位"，去重保序，最多 limit 条。

    先删掉完整日期（2024年1月1日 / 1月1日），否则 "1月" "1日" 会被当成金额类事实。
    """
    text = DATE.sub(" ", BOILER.sub("", (answer or "").translate(FW)))
    out, seen = [], set()
    for m in FACT.finditer(text):
        num, unit = m.group(1), m.group(2)
        key = num + unit
        if key in seen:
            continue
        # "3个月" 与 "3 个月" 归一后都能匹配；"12月" 这种日期误报靠单位区分不了，保留但记噪声
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


SENT_SPLIT = re.compile(r"[。；！？\n]")


def sentences(answer: str) -> list[str]:
    """官方答案拆句（去掉客套与页脚），用于"句子覆盖"这个宽口径。"""
    text = BOILER.sub("", (answer or "").translate(FW))
    out = []
    for part in SENT_SPLIT.split(text):
        t = norm(part)
        if len(t) >= 10:            # 太短的句子（"可以。"）没有信息量
            out.append(t)
    return out


def rgrams(text: str, n: int = 4) -> set[str]:
    t = norm(text)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def covered_sentences(answer: str, blob: str, thr: float = 0.6) -> tuple[int, int]:
    """官方答案里有几句话，能在我们给的文字里找到相近表述（4-gram 召回率 ≥ thr）。"""
    ss = sentences(answer)
    if not ss:
        return 0, 0
    B = rgrams(blob)
    hit = 0
    for s in ss:
        A = rgrams(s)
        if A and len(A & B) / len(A) >= thr:
            hit += 1
    return hit, len(ss)


def load_official() -> list[dict]:
    """官方问答 + v3 里的金标（用 (来源URL, 问题) 对齐）。"""
    gold = {}
    for q in json.loads(QUESTIONS.read_text("utf-8")):
        if q.get("category") == "official":
            gold[(q.get("source"), q.get("q"))] = q
    rows = []
    for p in sorted(HARVEST.glob("*.json")):
        d = json.loads(p.read_text("utf-8"))
        for qa in d.get("qa") or []:
            key = (d.get("url"), qa.get("q"))
            g = gold.get(key)
            if not g:
                continue
            rows.append({"q": qa["q"], "a": qa.get("a") or "",
                         "gold_doc": g["gold_doc"], "id": g["id"],
                         "split": g.get("split"), "url": d.get("url")})
    return rows


def gold_text(doc_no: str) -> str:
    key = doc_no.replace("〔", "-").replace("〕", "-")
    p = Path("data/processed") / f"{key}.json"
    if not p.exists():
        return ""
    d = json.loads(p.read_text("utf-8"))
    return norm("".join(b["text"] for b in d.get("blocks") or []))


def run(limit: int = 0) -> dict:
    from app.answer import cards
    from app.retrieval.search import search_result

    rows = load_official()
    if limit:
        rows = rows[:limit]

    class S:
        pass

    results = []
    for r in rows:
        fs = facts(r["a"])
        if not fs:
            continue
        ans = search_result(r["q"], k=5)
        st = S()
        st.answer = {"kind": ans.kind, "reason": ans.reason, "searched": ans.searched,
                     "confidence": ans.confidence, "as_of": ans.as_of}
        st.judgement = {}
        st.year = None
        st.hits = [{"doc_no": h.clause.doc_no, "title": h.clause.title,
                    "citation": h.clause.citation, "page": h.clause.page,
                    "path": list(h.clause.path), "text": h.child or h.clause.text,
                    "score": round(h.score, 3),
                    "related": [{"path": r.get("path") or [], "page": r.get("page"),
                                 "text": r.get("text") or ""} for r in (getattr(h, "related", []) or [])]}
                   for h in ans.hits]
        card = cards.build(st)
        blob = norm(card.get("headline", "") + " " + " ".join(
            (it.get("quote") or "") for it in card.get("items") or []) + " " +
            " ".join((e.get("quote") or "") for e in card.get("evidence") or []))
        got = [f for f in fs if f in blob]
        wide = norm(" ".join((h.child or h.clause.text) for h in ans.hits))
        got_wide = [f for f in fs if f in wide]
        gt = gold_text(r["gold_doc"])
        got_doc = [f for f in fs if f in gt]
        s_hit, s_all = covered_sentences(r["a"], blob)
        s_hit_wide, _ = covered_sentences(r["a"], norm(wide))
        hit_doc = any(h.clause.doc_no == r["gold_doc"] for h in ans.hits)
        results.append({
            "id": r["id"], "q": r["q"], "card": card["kind"], "answer_kind": ans.kind,
            "facts": fs, "covered": got, "covered_in_candidates": got_wide,
            "covered_in_gold_doc": got_doc,
            "sent_hit": s_hit, "sent_all": s_all, "sent_hit_candidates": s_hit_wide,
            "gold_doc": r["gold_doc"],
            "gold_in_candidates": hit_doc,
        })

    n = len(results)
    answered = [x for x in results if x["card"] != "refusal"]
    full = [x for x in results if len(x["covered"]) == len(x["facts"])]
    partial = [x for x in results if x["covered"]]
    total_facts = sum(len(x["facts"]) for x in results)
    got_facts = sum(len(x["covered"]) for x in results)
    got_wide = sum(len(x["covered_in_candidates"]) for x in results)
    s_hit = sum(x["sent_hit"] for x in results)
    s_all = sum(x["sent_all"] for x in results)
    s_hit_wide = sum(x["sent_hit_candidates"] for x in results)
    got_doc = sum(len(x["covered_in_gold_doc"]) for x in results)
    return {
        "n": n, "answered": len(answered),
        "full": len(full), "partial": len(partial), "none": n - len(partial),
        "fact_coverage": round(got_facts / total_facts, 3) if total_facts else 0.0,
        "facts": [total_facts, got_facts],
        "fact_coverage_candidates": round(got_wide / total_facts, 3) if total_facts else 0.0,
        "fact_coverage_gold_doc": round(got_doc / total_facts, 3) if total_facts else 0.0,
        "sent_coverage": round(s_hit / s_all, 3) if s_all else 0.0,
        "sent_coverage_candidates": round(s_hit_wide / s_all, 3) if s_all else 0.0,
        "sentences": [s_all, s_hit],
        "gold_in_candidates": sum(1 for x in results if x["gold_in_candidates"]),
        "rows": results,
    }


def report(res: dict) -> str:
    n = res["n"]
    lines = [
        "# 答案级评测：卡里有没有官方答案的关键事实", "",
        f"- 题数 **{n}**（有官方答案、且答案含数字事实）",
        f"- 作答 **{res['answered']}**（{res['answered']/n:.0%}），"
        f"拒答 **{n-res['answered']}**",
        f"- 卡里覆盖 **{res['fact_coverage']:.1%}**（{res['facts'][1]}/{res['facts'][0]} 个数字+单位）",
        f"- 候选条款里覆盖 **{res['fact_coverage_candidates']:.1%}**（检索层天花板，差距=展示截断）",
        f"- 金标文件全文覆盖 **{res['fact_coverage_gold_doc']:.1%}**（事实确实在那份文件里）",
        "",
        "> 三层的差就是丢在哪一层：卡 → 候选 = 展示截断/选条；候选 → 全文 = 条款级选错；",
        "> 全文之外的那部分 = 指标盲区（官方答案引自另一份文件、中文数字、或金标本身错）。",
        f"- 全对 **{res['full']}**、部分对 **{res['partial']}**、全错 **{res['none']}**",
        f"- 金标文件在候选里 **{res['gold_in_candidates']}/{n}**", "",
        "| 题 | 卡 | 事实 | 覆盖 | 金标在候选 |", "|---|---|---|---|---|",
    ]
    for x in res["rows"]:
        lines.append(f"| {x['id']} | {x['card']} | {len(x['facts'])} | "
                     f"{len(x['covered'])}/{len(x['facts'])} | "
                     f"{'✓' if x['gold_in_candidates'] else '✗'} |")
    lines += ["", "## 逐题明细", ""]
    for x in res["rows"]:
        miss = [f for f in x["facts"] if f not in x["covered"]]
        if miss:
            lines.append(f"- `{x['id']}` 缺：{'、'.join(miss)}  \n  {x['q'][:60]}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.limit)
    print(f"n={res['n']} 作答={res['answered']} 覆盖={res['fact_coverage']:.1%} "
          f"全对={res['full']} 部分={res['partial']} 全错={res['none']}")
    if args.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report(res), "utf-8")
        (REPORT.with_suffix(".json")).write_text(
            json.dumps({k: v for k, v in res.items() if k != "rows"},
                       ensure_ascii=False, indent=2), "utf-8")
        print(f"→ {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
