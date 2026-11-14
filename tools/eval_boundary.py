"""边界抽检：20 条条款，判"上边界 / 下边界"对不对。

为什么要人判：结构不变量只能证明"区间自洽"，证明不了"切点对"。
一条把下半句切到下一段的条款，区间照样自洽。

判据（每条只看四段文字）：

  上一行末尾   条款开始前的原文
  本条开头     应以编号开头，且编号前的文字不应被截断
  本条结尾     应以句号/分号收尾，不能停在半句
  下一行开头   应是新的编号或空

判定：上边界 ok / 下边界 ok / 错误类型（截断、包含下一条、从中间开始…）
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

DATA = Path("data/processed")
OUT = Path("eval/boundary_sheet.md")


def ctx(text: str, n: int = 60) -> str:
    return "".join(text.split())[:n]


def tail(text: str, n: int = 40) -> str:
    return "".join(text.split())[-n:]


def build(n: int = 20, seed: int = 7, out: Path = OUT) -> None:
    rng = random.Random(seed)
    docs = []
    for f in sorted(DATA.glob("*.json")):
        rec = json.loads(f.read_text("utf-8"))
        cl = [c for c in (rec.get("clauses") or []) if c["level"] >= 3]
        if len(cl) >= 6:
            docs.append((rec, cl))
    rng.shuffle(docs)

    picked: list[tuple[dict, dict, int, int]] = []
    # 每个文档抽 3–4 条，层级尽量分散，凑满 n
    per = max(1, n // 6)
    for rec, cl in docs:
        if len(picked) >= n:
            break
        by_level: dict[int, list[dict]] = {}
        for c in cl:
            by_level.setdefault(c["level"], []).append(c)
        levels = sorted(by_level)
        rng.shuffle(levels)
        take = 0
        for lv in levels:
            if take >= per or len(picked) >= n:
                break
            picked.append((rec, rng.choice(by_level[lv]), lv, len(cl)))
            take += 1

    lines = ["# 边界抽检 20 条", "",
             "每条给四段：上一行末尾 / 本条开头 / 本条结尾 / 下一行开头。",
             "判：上边界对不对、下边界对不对、错在哪。", ""]
    meta = []
    for i, (rec, c, lv, total) in enumerate(picked, 1):
        idx = rec["clauses"].index(c)
        prev = rec["clauses"][idx - 1] if idx > 0 else None
        nxt = rec["clauses"][idx + 1] if idx + 1 < len(rec["clauses"]) else None
        meta.append({"n": i, "doc": rec.get("doc_no"), "path": c["path"], "level": c["level"],
                     "start": c["start"], "end": c["end"], "page": c["page"]})
        lines += [f"## {i}. {rec.get('doc_no')} · `{c['path']}`（L{c['level']}，第 {i}/{total} 条文档内第 {idx+1} 条）",
                  "",
                  f"- 上一行末尾：`{tail(prev['text']) if prev else '（文档开头）'}`",
                  f"- **本条开头**：`{ctx(c['text'], 80)}`",
                  f"- **本条结尾**：`{tail(c['text'], 70)}`",
                  f"- 下一行开头：`{ctx(nxt['text'], 50) if nxt else '（文档结尾）'}`",
                  "", "判：上边界 ☐对 ☐错　下边界 ☐对 ☐错　错在：", ""]

    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), "utf-8")
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    print(f"→ {out}（{len(picked)} 条，{len({m['doc'] for m in meta})} 份文档）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="eval/boundary_sheet.md")
    a = ap.parse_args()
    build(a.n, a.seed, Path(a.out))
