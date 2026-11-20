"""把"答错了"归因到**最早出错的那一步**，而不是笼统说"检索不准"。

顺序即优先级：S1 语料 → S2 解析 → S3 抽取 → S4 结构化 → S5 检索 → S6 判定 → S7 评测。
前面的错会伪装成后面的错，所以必须**从前往后查**，第一个不成立的阶段就是根因。

用法：
    python -m tools.diagnose.attribute            # 跑 dev，出报告
    python -m tools.diagnose.attribute --split test
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.domain.validity import load as load_rels
from app.domain.validity import status_at
from app.retrieval.search import search_result, tokenize

PROCESSED = Path("data/processed")
QS = Path("eval/questions_v2.json")
STAGES = ["S7", "S1", "S2", "S3", "S4", "S5", "S6", "OK"]


@dataclass
class Diagnosis:
    id: str
    question: str
    gold_doc: str
    stage: str
    evidence: str
    sub: str = ""


def _load_docs() -> dict[str, dict]:
    out = {}
    for p in PROCESSED.glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        if d.get("doc_no"):
            out[d["doc_no"]] = d
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _is_hit(h, q: dict) -> bool:
    from tools.eval_v2 import is_hit
    return is_hit(h, q["gold_doc"], q.get("gold_title", ""))


def attribute(q: dict, docs: dict[str, dict], rels: dict) -> Diagnosis:
    """返回这条题的**最早失败阶段**；答对了返回 OK。"""
    cat = q.get("category")
    a = search_result(q["q"], k=50)

    # 负样本（out_of_scope）：正确结局是拒答
    if cat == "out_of_scope":
        if not a.hits:
            return Diagnosis(q["id"], q["q"], q.get("gold_doc") or "-", "OK", "正确拒答")
        return Diagnosis(q["id"], q["q"], q.get("gold_doc") or "-", "S5",
                         f"应拒答却答了（{a.hits[0].clause.doc_no}）", sub="误答")

    # 正样本：top-1 命中就算这一步没问题
    if a.hits and _is_hit(a.hits[0], q):
        return Diagnosis(q["id"], q["q"], q["gold_doc"], "OK", "top-1 命中")

    gold = q["gold_doc"]
    year = int(re.search(r"(20\d{2})", q.get("q") or "").group(1)) if re.search(r"(20\d{2})", q.get("q") or "") else None

    # S7 评测集本身：金标在提问年份已失效 → 判错不是系统的错
    if year:
        d = docs.get(gold)
        if d and status_at(d, as_of=f"{year}-12-31", rels=rels).status in ("repealed", "expired"):
            return Diagnosis(q["id"], q["q"], gold, "S7", f"金标在 {year} 年已失效")

    # S1 语料：这份文件根本没进来
    if gold not in docs:
        return Diagnosis(q["id"], q["q"], gold, "S1", "金标文件不在语料里")

    d = docs[gold]
    st = d.get("stats") or {}

    # S2 解析：有字节没文字
    if not st.get("chars") or st.get("chars", 0) < 40:
        return Diagnosis(q["id"], q["q"], gold, "S2", f"解析只得到 {st.get('chars', 0)} 字")

    # S3 抽取：文号是猜的 / 没有日期
    if d.get("doc_no_source") == "text" or not (d.get("issued_at") or d.get("effective_from")):
        return Diagnosis(q["id"], q["q"], gold, "S3",
                         f"doc_no_source={d.get('doc_no_source')}, issued_at={d.get('issued_at')}")

    # S4 结构化：整份没有条款，或问句词都在 blocks 里、却不在任何 clause 里
    clauses = d.get("clauses") or []
    if not clauses:
        return Diagnosis(q["id"], q["q"], gold, "S4", "整份文件 0 条款")
    qb = set(tokenize(q["q"])) - {""}
    in_blocks = any(w in _norm("".join(b["text"] for b in d.get("blocks") or [])) for w in qb)
    in_clause = any(w in _norm(c["text"]) for c in clauses for w in qb)
    if in_blocks and not in_clause:
        return Diagnosis(q["id"], q["q"], gold, "S4", "问句词在 blocks 里但不在任何条款里（切分丢失）")

    # S5 检索：金标在不在候选里、排第几
    rank = next((i for i, h in enumerate(a.hits, 1)
                 if h.clause.doc_no == gold), 0)
    if not a.hits:
        return Diagnosis(q["id"], q["q"], gold, "S5", f"整体拒答（{a.kind}）：{a.reason[:40]}", sub="闸门误拒")
    if rank == 0:
        return Diagnosis(q["id"], q["q"], gold, "S5", f"金标不在 top-50（返回 {len(a.hits)} 条）", sub="召回失败")
    if rank > 1:
        return Diagnosis(q["id"], q["q"], gold, "S5", f"金标排第 {rank}", sub="排序")

    # 走到这里说明 top-1 不是金标；S6 只在"检索对了但结论错"时才背锅，本工具暂不进
    return Diagnosis(q["id"], q["q"], gold, "S5", "未分类的检索失败", sub="其他")


def run(split: str = "dev") -> dict:
    questions = [q for q in json.loads(QS.read_text("utf-8"))
                 if q.get("split") == split and not q.get("ambiguous") and not q.get("stale")]
    skipped = [q for q in json.loads(QS.read_text("utf-8"))
               if q.get("split") == split and (q.get("ambiguous") or q.get("stale"))]
    docs = _load_docs()
    rels = load_rels()
    rows = [attribute(q, docs, rels) for q in questions]
    by_stage: dict[str, list[Diagnosis]] = {}
    for r in rows:
        by_stage.setdefault(r.stage, []).append(r)
    return {"split": split, "total": len(rows), "skipped": len(skipped), "rows": rows,
            "by_stage": {k: v for k, v in by_stage.items()}}


def report(res: dict) -> str:
    total = res["total"]
    lines = [f"# 归因报告（{res['split']}，n={total}）", "",
             "按**最早出错阶段**归类；前面的错会伪装成后面的错。", "",
             "| 阶段 | 条数 | 占比 | 含义 |", "|---|---|---|---|"]
    label = {"S7": "评测集本身过期/歧义", "S1": "语料缺失", "S2": "解析失败",
             "S3": "抽取错", "S4": "切分丢失", "S5": "检索排序/拒答", "S6": "判定缺失", "OK": "正确"}
    for st in STAGES:
        rows = res["by_stage"].get(st)
        if not rows:
            continue
        lines.append(f"| {st} | {len(rows)} | {len(rows)/total:.0%} | {label[st]} |")
    s5 = res["by_stage"].get("S5") or []
    if s5:
        subs: dict[str, int] = {}
        for r in s5:
            subs[r.sub or "其他"] = subs.get(r.sub or "其他", 0) + 1
        lines.append("**S5 再拆**：" + "，".join(f"{k} {v}" for k, v in
                                              sorted(subs.items(), key=lambda x: -x[1])))
        lines.append("")
    for st in STAGES:
        rows = res["by_stage"].get(st)
        if not rows:
            continue
        title = f"## {st} · {label[st]}（{len(rows)}）"
        if st == "S5":
            title += " — 按子类排"
            rows = sorted(rows, key=lambda r: r.sub)
        lines.append(title)
        for r in rows[:8]:
            lines.append(f"- `{r.id}` {r.question[:34]} — {r.evidence}")
        if len(rows) > 8:
            lines.append(f"- …另有 {len(rows)-8} 条")
        lines.append("")
    first = next((st for st in STAGES if st not in ("OK",) and res["by_stage"].get(st)), None)
    ok = len(res["by_stage"].get("OK") or [])
    lines.append(f"**先修哪里**：{first}（最早的失败阶段优先，修它会让下游一批问题同时消失）")
    lines.append("")
    lines.append(f"命中 top-1：{ok}/{total} = {ok/total:.0%}；其余 {total-ok} 条按上表归因。")
    lines.append("")
    lines.append("**能力缺口（不算答错）**：S6 判定器目前只覆盖落户，其余事项即使检索对了也给不出结论。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.split)
    text = report(res)
    if args.write:
        out = Path("data/reports/attribution.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, "utf-8")
        print("→", out)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
