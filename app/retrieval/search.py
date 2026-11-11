"""检索：问题 → 条款 + 出处。不做判定，只负责"找到并证明它在哪"。

两个刻意的设计：
1. **返回空是合法结果**。最高分低于阈值时返回 insufficient，而不是硬凑 top-1——
   下游据此给出"证据不足"，而不是拿一段不相干的条文假装回答。
2. **效力状态参与过滤但不隐藏**。判定只允许用 effective；检索可以召回 expired/unknown，
   但必须把状态带回给调用方，由它决定怎么说。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import bm25s

from app.retrieval.index import INDEX, tokenize
from app.retrieval.records import Clause

MIN_SCORE = 3.0      # BM25 绝对分；低于它基本是"只共享了两个字"
QUESTION_WORDS = {"哪些", "什么", "怎么", "如何", "多少", "是否", "能否", "可以",
                  "需要", "请问", "一下", "几天", "多久", "有没有"}


@dataclass
class Hit:
    clause: Clause
    score: float
    coverage: float = 0.0   # 查询词有多少比例真的出现在这条条款里
    versions: int = 1       # 同名文件的历史版本数（含本条）


def _load() -> tuple[bm25s.BM25, list[Clause]]:
    retriever = bm25s.BM25.load(str(INDEX / "bm25"))
    clauses = [Clause(**json.loads(l)) for l in
               (INDEX / "clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    return retriever, clauses


def search(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
           min_score: float = MIN_SCORE, min_coverage: float = 0.0) -> list[Hit]:
    retriever, clauses = _load()
    qt = [t for t in tokenize(query) if t not in QUESTION_WORDS]
    if not qt:
        return []
    idx, scores = retriever.retrieve([qt], k=min(len(clauses), max(k * 4, 40)))
    out: list[Hit] = []
    for i, s in zip(idx[0], scores[0]):
        c = clauses[int(i)]
        if status is not None and c.status not in status:
            continue
        if float(s) < min_score:
            continue
        ctext = set(tokenize(c.text))
        cov = len(set(qt) & ctext) / max(1, len(set(qt)))
        if cov < min_coverage:
            continue
        out.append(Hit(c, float(s), round(cov, 2)))
    return _collapse(out)[:k]


def _norm_title(t: str) -> str:
    return "".join(ch for ch in t if ch not in " \u3000")


def _collapse(hits: list[Hit]) -> list[Hit]:
    """两份事：同一份文件只留最相关的一条；同名文件（逐年延期的"关于调整…的通知"）
    合并成一条并记下版本数，否则 top-5 会被 2016/2017/2018 三个版本占满。"""
    seen_doc: set[str] = set()
    by_title: dict[str, Hit] = {}
    order: list[str] = []
    for h in hits:
        c = h.clause
        if c.doc_no in seen_doc:
            continue
        seen_doc.add(c.doc_no)
        key = _norm_title(c.title) or c.doc_no
        if key not in by_title:
            by_title[key] = h
            order.append(key)
        else:
            by_title[key].versions += 1
        h.versions = 1
    return [by_title[k] for k in order]


def evidence_gap(query: str, hits: list[Hit]) -> str | None:
    """回答"为什么答不了"，而不是只说答不了。"""
    if not hits:
        return "没有检索到相关条款；可能是该事项不在已接入语料范围内，或问法与条文用词差异过大。"
    top = hits[0]
    if top.coverage < 0.5:
        return (f"最相关条款只覆盖了 {top.coverage:.0%} 的提问词"
                f"（《{top.clause.doc_no}》{top.clause.citation.split('》')[-1]}），"
                "大概率答非所问，不给结论。")
    return None
