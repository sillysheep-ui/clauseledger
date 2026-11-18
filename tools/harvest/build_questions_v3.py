"""把官方问答扩成更大的评测集（v3）。

背景：v2 有 168 条 official 题，dev 只有 94 条。按功效计算，要检测 5pp 差异
需要约 280 条题，94 条只能检测 ~12pp。所以从官方问答里补题。

四条纪律（每条都是踩过的坑）：
1. **标题相似 ≥ 0.75 + 年份一致**：0.75–0.85 那一段几乎全是"2024 年问答 ↔ 2021 年政策"
   的错配，只看相似度会造出错误金标。
2. **最长公共子串 ≥ 10**：挡住"同一个通知名的不同年份版本"这种近似标题。
3. **问题里的年份必须落在金标有效期里**：`2024年失业保险金` 不能挂在 2019 年、2021 年就
   失效的那份文件上。
4. **同一份文件最多 3 条题**，否则一份文件主导分母。

v2 的题**原样保留**（id、split 都不动），新题只追加——这样 v2 的历史数字还能对上。

用法：
    python -m tools.harvest.build_questions_v3 --dry
    python -m tools.harvest.build_questions_v3 --write
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

PROCESSED = Path("data/processed")
HARVEST = Path("data/harvest/qa")
META = Path("data/index/docs_meta.json")
OUT = Path("eval/questions_v3.json")
V2 = Path("eval/questions_v2.json")

# 脱离原文就没法回答的表述
CONTEXT_DEP = re.compile(r"(本办法|该办法|上述|此项|此条|这次|此次|本通知|该通知|此文|该项)")
YEAR_WORD = re.compile(r"(今年|去年|明年|前年)")
PREFIX = re.compile(r"^\s*(答|问)\s*[:：]\s*")
NOISE = re.compile(r"^(请问|问：)")
YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
TITLE_YEAR = YEAR


def norm_title(t: str) -> str:
    t = re.sub(r"[《》〈〉\s　]", "", t)
    t = re.sub(r"[（(].*?[)）]$", "", t)
    return t


def lcs(a: str, b: str) -> int:
    m = difflib.SequenceMatcher(None, a, b)
    return m.find_longest_match(0, len(a), 0, len(b)).size


def load_corpus() -> list[dict]:
    out = []
    for f in sorted(PROCESSED.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        if not d.get("doc_no"):
            continue
        out.append({"doc_no": d["doc_no"], "title": d.get("title") or "",
                    "norm": norm_title(d.get("title") or "")})
    return out


def match(title: str, corpus: list[dict], *, cut: float = 0.75, year: str = "",
          min_lcs: int = 10) -> tuple[dict | None, float]:
    n = norm_title(title)
    if not n:
        return None, 0.0
    best, score = None, 0.0
    for c in corpus:
        r = difflib.SequenceMatcher(None, n, c["norm"]).ratio()
        if r > score:
            best, score = c, r
    if not best or score < cut:
        return None, score
    my = TITLE_YEAR.search(best["title"])
    if my and year and my.group(0) != year:
        return None, score
    if lcs(n, best["norm"]) < min_lcs:
        return None, score
    return best, score


def clean(q: str, year: str) -> str | None:
    q = PREFIX.sub("", q or "").strip()
    q = NOISE.sub("", q).strip()
    if not (8 <= len(q) <= 70):
        return None
    if CONTEXT_DEP.search(q):
        return None
    if YEAR_WORD.search(q):
        if not year:
            return None
        q = YEAR_WORD.sub(f"{year}年", q)
    if q.count("？") + q.count("?") > 1:
        q = q.split("？")[0].split("?")[0] + "？"
    return q if q.endswith(("？", "?")) else q + "？"


def in_window(q: str, meta: dict) -> bool:
    """问题里提到的年份必须落在金标有效期内（有日期才判）。"""
    yrs = {int(y) for y in YEAR.findall(q)}
    if not yrs:
        return True
    f0, f1 = meta.get("effective_from"), meta.get("effective_to")
    if f0 and any(y < int(f0[:4]) for y in yrs):
        return False
    if f1 and any(y > int(f1[:4]) for y in yrs):
        return False
    # 文件自己一个日期都没有 → 无法核对题目年份，宁可不出这道题
    if not (f0 or f1 or meta.get("issued_at")):
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--cap", type=int, default=3)
    a = ap.parse_args()

    corpus = load_corpus()
    meta = json.loads(META.read_text("utf-8")) if META.exists() else {}
    old = json.loads(V2.read_text("utf-8")) if V2.exists() else []
    seen_old = {re.sub(r"\s", "", q["q"]) for q in old}

    rows: list[dict] = []
    seen: set[str] = set()
    per_doc: Counter = Counter()
    stats = Counter()

    for f in sorted(HARVEST.glob("*.json")):
        art = json.loads(f.read_text("utf-8"))
        qa = art.get("qa") or []
        if not qa:
            continue
        year = (art.get("date") or "")[:4]
        gold, score = match(art.get("title") or "", corpus, year=year)
        if not gold:
            stats["文章未匹配"] += 1
            continue
        stats["文章匹配"] += 1
        for item in qa:
            q = clean(item.get("q") or "", year)
            if not q:
                stats["问题被过滤"] += 1
                continue
            key = re.sub(r"\s", "", q)
            if key in seen or key in seen_old:
                stats["重复"] += 1
                continue
            if not in_window(q, meta.get(gold["doc_no"], {})):
                stats["年份越界"] += 1
                continue
            if per_doc[gold["doc_no"]] >= a.cap:
                stats["超出单文档上限"] += 1
                continue
            seen.add(key)
            per_doc[gold["doc_no"]] += 1
            rows.append({
                "q": q, "category": "official",
                "gold_doc": gold["doc_no"], "gold_title": gold["title"],
                "source_title": art.get("title") or "",
                "source_url": art.get("url") or "",
                "match_score": round(score, 3),
            })

    random.Random(7).shuffle(rows)
    n = len(rows)
    dev_end = int(n * 0.60)
    test_end = int(n * 0.85)
    for i, r in enumerate(rows):
        r["id"] = f"V3-{i+1:03d}"
        r["split"] = "dev" if i < dev_end else ("test" if i < test_end else "holdout")
        r["source"] = "v3-new"

    merged = old + rows
    print(f"新题 {n} 条，覆盖 {len(per_doc)} 份文件")
    print("过滤统计:", dict(stats))
    print("新题切分:", Counter(r["split"] for r in rows))
    print("v2 原题:", len(old), "→ 合计", len(merged))
    print("合计 official 切分:", Counter(r["split"] for r in merged if r["category"] == "official"))
    print("\n新题样例:")
    for r in rows[:8]:
        print(f"  [{r['split']}] {r['q'][:40]} → {r['gold_doc']}（sim {r['match_score']}）")

    if a.write:
        OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", "utf-8")
        h = hashlib.sha256(OUT.read_bytes()).hexdigest()
        print(f"\n→ {OUT}")
        print(f"sha256 {h}")


if __name__ == "__main__":
    main()
