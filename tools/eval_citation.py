"""实验 C：引用级金标——把"定位得到"和"支撑得住"分开。

现在唯一和引用有关的指标是 `citation_verifiable`，它只做一件事：
    引用的那段文本，能不能在原文里按字符位置找到。
这测的是 CLR（can locate），100% 不代表引文能回答提问。

这个脚本产出人工标注表：
    eval/citation_sheet.md     给人看/填的表
    eval/citation_sheet.json   机读
并对每条自动算好 CLR（可定位）与文件级命中，供对照。

标注列（人填）：
    支撑 S/P/N   S=条款足以回答  P=只答一半（缺条件/缺数字/需再查）  N=答不了
    适用 A/P/X   A=适用提问者情境  P=部分（如只对某类人）  X=不适用（城市/年份/身份不符）

用法：
    python -m tools.eval_citation --build          # 生成标注表
    python -m tools.eval_citation --score          # 读回标注，算 CSR/适用率
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from types import SimpleNamespace

from app.retrieval.search import search_result

QS = Path("eval/questions_v2.json")
SHEET = Path("eval/citation_sheet.json")
MD = Path("eval/citation_sheet.md")
DOCS = Path("data/processed")


def load_doc(doc_no: str) -> dict | None:
    for f in DOCS.glob("*.json"):
        d = json.loads(f.read_text("utf-8"))
        if d.get("doc_no") == doc_no:
            return d
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def clr(clause, rec) -> dict:
    """可定位：字符区间落在原文长度内、该区间文本与条款文本一致、页码对得上。

    一致性用"去空白后包含"判断：条款文本是块文本按分隔符拼的，
    直接比原串会被换行/空格卡住（曾经因此误判 9 条引用不可定位）。
    """
    text = "".join(b["text"] for b in rec["blocks"])
    ok_range = 0 <= clause.start < clause.end <= len(text) + 200
    ok_text = _norm(clause.text)[:60] in _norm(text)
    ok_page = True
    if clause.page:
        blk = next((b for b in rec["blocks"] if b.get("i") == clause.block_index), None)
        ok_page = bool(blk and blk.get("page") == clause.page)
    return {"range": ok_range, "text": ok_text, "page": ok_page,
            "ok": bool(ok_range and ok_text and ok_page)}


def build(n: int = 30, split: str = "test") -> None:
    qs = [q for q in json.loads(QS.read_text("utf-8"))
          if q["split"] == split and q["category"] == "official"]
    rows = []
    for q in qs:
        if len(rows) >= n:
            break
        r = search_result(q["q"])
        if r.outcome != "answer" or not r.hits:
            continue
        h = r.hits[0]
        c = h.clause
        rec = load_doc(c.doc_no)
        if rec is None:
            continue
        rows.append({
            "id": q["id"], "q": q["q"], "gold_doc": q["gold_doc"],
            "cited_doc": c.doc_no, "cited_title": c.title,
            "path": "/".join(c.path), "page": c.page,
            "start": c.start, "end": c.end,
            "status": c.status, "effective_from": c.effective_from,
            "effective_to": c.effective_to,
            "text": c.text[:600],
            "file_hit": int(c.doc_no == q["gold_doc"]
                            or (bool(q.get("gold_title")) and c.title == q["gold_title"])),
            "clr": clr(c, rec),
            "support": "", "applicable": "",
        })
    SHEET.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    lines = [f"# 引用级标注表（{split}，{len(rows)} 条）", "",
             "标注：`support` S=足以回答 P=只答一半 N=答不了；`applicable` A=适用 P=部分 X=不适用", ""]
    for i, r in enumerate(rows, 1):
        lines += [
            f"## {i}. {r['id']} {r['q']}",
            "",
            f"- 引用：**{r['cited_doc']}** {r['path']}（第 {r['page']} 页，{r['status']}）",
            f"- 金标：{r['gold_doc']}｜文件级命中：{'是' if r['file_hit'] else '否'}｜CLR：{'通过' if r['clr']['ok'] else '不通过'}",
            "",
            "```",
            r["text"].strip(),
            "```",
            "",
            "- support: / applicable: ",
            "",
        ]
    MD.write_text("\n".join(lines), "utf-8")
    print(f"→ {MD}（{len(rows)} 条）\n→ {SHEET}")


def recheck() -> None:
    """只重算 CLR，不动人工标注。"""
    rows = json.loads(SHEET.read_text("utf-8"))
    cache: dict[str, dict] = {}
    for r in rows:
        doc = r["cited_doc"]
        if doc not in cache:
            rec = load_doc(doc)
            cache[doc] = rec
        rec = cache[doc]
        if rec is None:
            r["clr"] = {"range": False, "text": False, "page": False, "ok": False}
            continue
        c = SimpleNamespace(text=r["text"], start=r["start"], end=r["end"],
                            page=r["page"], block_index=r.get("block_index", -1))
        r["clr"] = clr(c, rec)
    SHEET.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    ok = sum(r["clr"]["ok"] for r in rows)
    print(f"CLR {ok}/{len(rows)} = {ok/len(rows):.0%}")


def score() -> None:
    rows = json.loads(SHEET.read_text("utf-8"))
    done = [r for r in rows if r["support"].strip()]
    if not done:
        print("还没有标注：在 eval/citation_sheet.json 里填 support/applicable")
        return
    n = len(done)
    s = sum(r["support"].upper().startswith("S") for r in done)
    p = sum(r["support"].upper().startswith("P") for r in done)
    nn = sum(r["support"].upper().startswith("N") for r in done)
    a = sum(r["applicable"].upper().startswith("A") for r in done)
    x = sum(r["applicable"].upper().startswith("X") for r in done)
    clr_ok = sum(r["clr"]["ok"] for r in done)
    print(f"n={n}  CLR（可定位）{clr_ok}/{n} = {clr_ok/n:.0%}")
    print(f"CSR 支撑充分 {s}/{n} = {s/n:.0%}｜部分 {p}｜不足 {nn}")
    print(f"适用 A {a}/{n} = {a/n:.0%}｜不适用 X {x}")
    print(f"「可定位但支撑不足」= {(nn + p)}/{n} = {(nn + p)/n:.0%}"
          "  ← 这就是 citation_verifiable=100% 掩盖的部分")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    if a.build:
        build(a.n, a.split)
    elif a.score:
        score()
    elif a.recheck:
        recheck()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
