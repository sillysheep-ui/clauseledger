"""词表体检：哪些词是白加的、哪些词该加还没加。

用法：
    python -m tools.lexicon_check            # 全部报告
    python -m tools.lexicon_check --top 60   # 看更多"高频但没进表"的候选

判据只有两条，不需要人工猜：
1. `segmentation` 里某个词在全部条款里 **一次都没出现** → 白加的（拼错、或语料里没有这个概念）
2. 某个 **二字以上** 的词出现次数很多，却不在 `segmentation` 里 → 可能被 jieba 切碎了
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.retrieval import lexicon
from app.retrieval.index import tokenize

CLAUSES = Path("data/index/clauses.jsonl")


def df_map(texts: list[str]) -> Counter:
    c: Counter = Counter()
    for t in texts:
        for w in set(tokenize(t)):
            c[w] += 1
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()

    lex = lexicon.load()
    texts = [json.loads(l)["text"] for l in CLAUSES.read_text("utf-8").splitlines()]
    df = df_map(texts)
    n = len(texts)

    print(f"# 词表体检（{n} 条条款，lexicon v{lex.version}）\n")
    print(f"- segmentation {len(lex.segmentation)} 个词 / question_words {len(lex.question_words)} / water {len(lex.water)}")

    dead = [w for w in lex.segmentation if df[w] == 0]
    print(f"\n## 一、白加的词（0 次出现）：{len(dead)} 个")
    for w in dead:
        print(f"- `{w}`")
    if not dead:
        print("- 无")

    used = [(w, df[w]) for w in lex.segmentation if df[w] > 0]
    if used:
        print("\n### 用得最多的分词词")
        for w, c in sorted(used, key=lambda x: -x[1])[:10]:
            print(f"- `{w}` {c} 条")

    known = set(lex.segmentation) | set(lex.question_words) | set(lex.water)
    cand = [(w, c) for w, c in df.items() if len(w) >= 2 and w not in known and c >= 20]
    cand.sort(key=lambda x: -x[1])
    print(f"\n## 二、高频但没进表的词（≥20 条，前 {a.top}）")
    print("> 不是每个都该加：先看它是不是已经被切成更小的词了。")
    for w, c in cand[: a.top]:
        print(f"- `{w}` {c}")


if __name__ == "__main__":
    main()
