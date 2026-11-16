"""建索引：把 560 份文件切成可检索的条款，落成 BM25 索引。

为什么是 BM25 而不是向量：本地没有可用的 embedding 服务（没装 ollama、没有 API key），
而政策检索的关键词信号很强（"丧假""工伤认定""缴费基数"都是硬词）。
向量是后续叠加，不是替代——混合召回的口径已经在 docs/ARCHITECTURE.md 里定了。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jieba
import bm25s

from app.retrieval.children import split_children
from app.retrieval.records import Clause, status_of

import os

PROCESSED = Path(os.environ.get("CLAUSELEDGER_PROCESSED", "data/processed"))
INDEX = Path(os.environ.get("CLAUSELEDGER_INDEX", "data/index"))

# 领域词：jieba 默认词典会把"丧假""居转户"切碎，这里补上
USER_WORDS = ["丧假", "居转户", "工伤认定", "缴费基数", "养老保险", "医疗保险",
              "失业保险", "生育保险", "公积金", "最低工资", "经济补偿", "劳动仲裁",
              "视同缴费", "过渡性养老金", "特殊工种", "提前退休", "遗属待遇"]

STOP = set("的了和与及或是在对按依据根据本条第款该项其这那为以等并")
RE_PUNCT = re.compile(r"[\s\u3000，。、；：！？（）()《》〈〉“”\"'·—…,.;:!?\[\]【】]+")


def tokenize(text: str) -> list[str]:
    text = RE_PUNCT.sub(" ", text)
    return [t for t in jieba.lcut(text) if len(t) > 1 and t not in STOP]


def _setup() -> None:
    for w in USER_WORDS:
        jieba.add_word(w)


def _path(p) -> list[str]:
    """存储里 path 是 "二、/（一）" 这样的斜杠串，不是数组。"""
    if isinstance(p, str):
        return [x for x in p.split("/") if x]
    return list(p or [])


def _is_leaf(c: Clause, paths: list[tuple]) -> bool:
    """父条款文本包含子条款全文，同时索引会造成同一段文字出现两三次。只留叶子。"""
    p = tuple(c.path)
    for other in paths:
        if len(other) > len(p) and other[: len(p)] == p:
            return False
    return True


def collect(min_chars: int = 10) -> list[Clause]:
    _setup()
    out: list[Clause] = []
    for f in sorted(PROCESSED.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        st = status_of(d.get("effective_to"), d.get("effective_from"))
        doc: list[Clause] = []
        for i, c in enumerate(d.get("clauses", [])):
            text = (c.get("text") or "").strip()
            if len(text) < min_chars:
                continue
            doc.append(Clause(
                id=f"{d['doc_no']}#{i}",
                doc_no=d["doc_no"], title=d.get("title", ""),
                path=_path(c.get("path")), level=c.get("level", 0),
                label=c.get("label", ""), text=text,
                page=c.get("page") or 0, block_index=c.get("block_index") or 0,
                char_offset=c.get("char_offset") or 0,
                start=c.get("start") or 0, end=c.get("end") or 0,
                status=st, effective_from=d.get("effective_from"),
                effective_to=d.get("effective_to"),
                url=d.get("url", ""), parser_version=d.get("parser_version", ""),
                carrier=c.get("carrier") or "page", extract=d.get("extract") or "native",
            ))
        if not doc:   # 1980–2000 年代的短通知常常整篇不分条，按段落兜底
            for j, b in enumerate(d.get("blocks", [])):
                text = (b.get("text") or "").strip()
                if len(text) < 30 or "<div" in text:
                    continue
                doc.append(Clause(
                    id=f"{d['doc_no']}#b{j}", doc_no=d["doc_no"], title=d.get("title", ""),
                    path=["正文"], level=0, label="", text=text,
                    page=b.get("page") or 0, block_index=j, char_offset=0,
                    start=0, end=0, status=st, effective_from=d.get("effective_from"),
                    effective_to=d.get("effective_to"), url=d.get("url", ""),
                    parser_version=d.get("parser_version", ""),
                    carrier=d.get("carrier") or "page", extract=d.get("extract") or "native"))

        # 条款路径只在**同一份文件内**唯一；跨文件比较会把"一、"当成别人的父节点
        paths = [tuple(c.path) for c in doc]
        out.extend(c for c in doc if _is_leaf(c, paths))
    return _dedupe(out)


def _dedupe(clauses: list[Clause]) -> list[Clause]:
    """同一段文字常随文件版本重复出现（2013/2015/2018 三份内容几乎一致）。
    只留一条：优先现行，其次发文日期最新。否则 top-5 会被同一句话占满。"""
    best: dict[str, Clause] = {}
    for c in clauses:
        key = "".join(c.text.split())[:120]
        old = best.get(key)
        if old is None:
            best[key] = c
            continue
        rank = lambda x: (x.status == "effective", x.effective_from or "", x.doc_no)
        if rank(c) > rank(old):
            best[key] = c
    return list(best.values())


def build_children(clauses: list[Clause]) -> list[dict]:
    """父子索引：索引建在子块上，返回时回填父条款。

    pid 是父条款在 clauses 列表里的下标——条款 id 里有 "#"，用它当键在
    jsonl 里不好定位；下标在同一个索引版本内是稳定的。
    """
    out: list[dict] = []
    for pid, c in enumerate(clauses):
        for k, (s, e) in enumerate(split_children(c.text)):
            out.append({"cid": f"{pid}:{k}", "pid": pid, "start": s, "end": e,
                        "text": c.text[s:e]})
    return out


def build() -> dict:
    _setup()
    clauses = collect()
    INDEX.mkdir(parents=True, exist_ok=True)
    (INDEX / "clauses.jsonl").write_text(
        "\n".join(json.dumps(c.to_dict(), ensure_ascii=False) for c in clauses), "utf-8")

    children = build_children(clauses)
    (INDEX / "children.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in children), "utf-8")

    # 索引建在子块上：长条款里的那一句话不再被几万字稀释
    corpus = [tokenize(x["text"]) for x in children]
    retriever = bm25s.BM25()
    retriever.index(corpus)
    retriever.save(str(INDEX / "bm25"))

    split_docs = sum(1 for pid in {x["pid"] for x in children}
                     if sum(1 for y in children if y["pid"] == pid) > 1)
    stats = {
        "clauses": len(clauses),
        "children": len(children),
        "split_clauses": split_docs,
        "docs": len({c.doc_no for c in clauses}),
        "chars": sum(len(c.text) for c in clauses),
        "child_chars": sum(len(x["text"]) for x in children),
        "by_status": {},
        "avg_tokens": round(sum(len(t) for t in corpus) / max(1, len(corpus)), 1),
    }
    for c in clauses:
        stats["by_status"][c.status] = stats["by_status"].get(c.status, 0) + 1
    (INDEX / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), "utf-8")
    return stats


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
