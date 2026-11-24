"""标准 RAG 主路的最小可跑版：检索 → 装配证据 → 模型生成 → 逐句核验。

用法：
    python -m tools.answer "失业保险金怎么领"
    python -m tools.answer "..." --no-llm      # 只看装配出来的证据
"""
from __future__ import annotations

import argparse
import sys

from app.answer import generate as G
from app.answer.context import estimate_tokens, evidence_for
from app.answer.verify import check
from app.retrieval.search import search_result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--budget", type=int, default=12000, help="证据字数上限（约 1.5 字/token）")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    ans = search_result(a.question, k=a.k)
    print(f"[检索] kind={ans.kind} hits={len(ans.hits)} "
          f"recall={ans.searched.get('recall')} agree={ans.searched.get('agree')}")
    if not ans.hits:
        print("[证据] 无")
        return 0

    ev = evidence_for(a.question)
    print(f"[证据] {len(ev)} 条 / {sum(len(e.text) for e in ev)} 字 / ≈{estimate_tokens(ev)} token")
    for e in ev:
        print(f"  {e.eid} {e.role:8s} {e.citation} ({len(e.text)}字)")

    if a.no_llm or not G.available():
        print("[生成] 跳过（--no-llm 或没有 key）")
        return 0

    d = G.generate(a.question, ev)
    if d is None:
        print("[生成] 失败 → 应退回截取式卡片")
        return 1

    chk = check(d, ev)
    print(f"[生成] {'缓存' if d.cached else '调用'} · {len(d.sentences)} 句 · "
          f"tokens={d.usage.get('total_tokens', '?')} · conf={d.confidence}")
    for s in d.sentences:
        print(f"  - {s['text']} {''.join('[' + c + ']' for c in s.get('cites', []))}")
    print(f"[核验] 保留 {len(chk.kept)} / 丢弃 {len(chk.dropped)} · "
          f"数字核对 {chk.numbers_checked} 个 · {'通过' if chk.ok else '有问题'}")
    for i in chk.issues:
        print(f"  ! {i}")
    if d.not_covered:
        print(f"[未覆盖] {'；'.join(d.not_covered)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
