"""口语→政策词的改写（只做加法，可审计）。

为什么需要：用户不会用政策语言提问。BM25 的分数完全建立在"提问词在条款里出现过"
之上，而"生娃""跳槽""被辞退""工龄"这些词在 574 份政策里一次都不出现——实词全部
落空，只剩下"能/怎么/多少"这类虚词在计分，正确条款自然排不上去。

这一层的做法刻意保守：

* **只加不减**：原词保留，只是在检索时额外带上政策用语。删词会把原本正确的检索带偏。
* **长词优先**："自己交社保"整体匹配，不会被"社保"两个字截断。
* **不猜条件**：只做词级映射，不改年份、数字、否定词（"不""不得""未"）。
* **可审计**：每次命中记 `searched["rewrites"]`，答案里能写"我按『失业保险金』查的"。

词表在 `data/fixtures/lexicon.json` 的 `colloquial` 表里，来源是百度下拉词（真实
用户问法）与政策文本的对照，**不取自评测集**——否则就是照答案改卷子。
"""
from __future__ import annotations

import os
from typing import NamedTuple

from app.retrieval import lexicon

ENABLED = os.environ.get("CLAUSELEDGER_REWRITE", "1").lower() not in ("0", "off", "false")
# 改写后端：rules（默认，手写表）/ llm（DeepSeek，失败自动退回 rules）
BACKEND = os.environ.get("CLAUSELEDGER_REWRITE_BACKEND", "rules").lower()


class Rewrite(NamedTuple):
    terms: list[str]          # 追加进检索的词
    hits: list[dict]          # [{"from": "生娃", "to": ["生育"]}]，给人看
    backend: str = "rules"

    @property
    def changed(self) -> bool:
        return bool(self.terms)


def _pairs() -> list[tuple[str, list[str]]]:
    pairs: list[tuple[str, list[str]]] = []
    for row in lexicon.colloquial():
        to = [t for t in row.get("to", []) if t]
        if not to:
            continue
        for f in row.get("from", []):
            if f:
                pairs.append((f, to))
    # 长词优先：否则"自己交社保"会先被"社保"命中
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def apply(query: str) -> Rewrite:
    """对问题做词级扩展。返回要追加的词与命中记录。

    `CLAUSELEDGER_REWRITE_BACKEND=llm` 时先问模型；模型不可用、超时、返回空，
    一律退回手写规则表——**永远不能因为模型挂了就让检索失去这层**。
    """
    if not query or not ENABLED:
        return Rewrite([], [])
    if BACKEND == "llm":
        from app.retrieval import llm_rewrite
        r = llm_rewrite.apply(query)
        if r.terms:
            return Rewrite(r.terms, r.hits, backend="llm")
    return _rules(query)


def _rules(query: str) -> Rewrite:
    q = query
    terms: list[str] = []
    hits: list[dict] = []
    for src, dst in _pairs():
        if src not in q:
            continue
        # 已经命中过长词的位置遮掉，避免"自己交社保"之后又被"社保"重复命中
        q = q.replace(src, "\u0000" * len(src))
        added = [t for t in dst if t not in terms and t not in query]
        if not added:
            continue
        terms.extend(added)
        hits.append({"from": src, "to": added})
    return Rewrite(terms, hits)
