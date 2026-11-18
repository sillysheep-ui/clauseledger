"""建索引：把 560 份文件切成可检索的条款，落成 BM25 索引。

为什么是 BM25 而不是向量：本地没有可用的 embedding 服务（没装 ollama、没有 API key），
而政策检索的关键词信号很强（"丧假""工伤认定""缴费基数"都是硬词）。
向量是后续叠加，不是替代——混合召回的口径已经在 docs/ARCHITECTURE.md 里定了。
"""
from __future__ import annotations

import json
import re
import re
from pathlib import Path

import jieba
import bm25s

from app.retrieval import lexicon

from app.retrieval.children import split_children
from app.retrieval.records import Clause, status_of

import os

PROCESSED = Path(os.environ.get("CLAUSELEDGER_PROCESSED", "data/processed"))
INDEX = Path(os.environ.get("CLAUSELEDGER_INDEX", "data/index"))

# 领域词：jieba 默认词典会把"丧假""居转户"切碎，这里补上
# 词表在 data/fixtures/lexicon.json，不在这里。理由见 app/retrieval/lexicon.py。
USER_WORDS = lexicon.segmentation_words()

STOP = set("的了和与及或是在对按依据根据本条第款该项其这那为以等并")
RE_PUNCT = re.compile(r"[\s\u3000，。、；：！？（）()《》〈〉“”\"'·—…,.;:!?\[\]【】]+")


_READY = False


def _setup(force: bool = False) -> None:
    """把用户词灌进 jieba。必须**自动**执行——

    原先只在 `collect()` / `build()` 里调，结果任何直接 `import tokenize` 的调用方
    （诊断脚本、词表体检）拿到的都是"没加用户词"的分词结果：17 个词里 11 个 df=0。
    分词对不对，不该取决于调用顺序。
    """
    global _READY
    if _READY and not force:
        return
    for w in USER_WORDS:
        jieba.add_word(w)
    _READY = True


def tokenize(text: str) -> list[str]:
    _setup()
    text = RE_PUNCT.sub(" ", text)
    return [t for t in jieba.lcut(text) if len(t) > 1 and t not in STOP]


def _path(p) -> list[str]:
    """存储里 path 是 "二、/（一）" 这样的斜杠串，不是数组。"""
    if isinstance(p, str):
        return [x for x in p.split("/") if x]
    return list(p or [])


def _is_leaf(c: Clause, paths: list[tuple]) -> bool:
    """父条款文本包含子条款全文，同时索引会造成同一段文字出现两三次。只留叶子。

    比较键必须带 part：一份文件里常装着通知 + 附件《办法》，两边都有「一、」，
    只比 path 会把附件里的「一、」当成正文「一、」的父条款，整份附件就被删光。
    """
    mine = tuple(c.path)
    for part, other in paths:
        if part == c.part and len(other) > len(mine) and other[: len(mine)] == mine:
            return False
    return True


def _with_part(c: dict, labels: list[str]) -> list[str]:
    """附件里的条款在路径前加「附件N ›」，否则和正文同号条款分不开。

    附录自己切块时 path 已经是「附件一」，再加一次会变成「附件一 › 附件一」。
    """
    path = _path(c.get("path"))
    part = c.get("part") or 0
    if part <= 0 or part - 1 >= len(labels):
        return path
    label = labels[part - 1]
    return path if path and path[0] == label else [label] + path


def collect(min_chars: int = 10) -> list[Clause]:
    _setup()
    out: list[Clause] = []
    for f in sorted(PROCESSED.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        st = status_of(d.get("effective_to"), d.get("effective_from"))
        # 附件里的《办法》有自己的一套编号，引用时必须写明"附件N ›"，否则和正文的第十八条分不开
        labels = [re.sub(r"\s+", "", (b.get("text") or "").strip())
                  for b in d.get("blocks", [])
                  if re.fullmatch(r"(附\s*录|附\s*表|附\s*件)\s*([0-9一二三四五六七八九十]{1,3})?",
                                  (b.get("text") or "").strip())
                  and len((b.get("text") or "").strip()) <= 12]
        doc: list[Clause] = []
        for i, c in enumerate(d.get("clauses", [])):
            text = (c.get("text") or "").strip()
            if len(text) < min_chars:
                continue
            doc.append(Clause(
                id=f"{d['doc_no']}#{i}",
                doc_no=d["doc_no"], title=d.get("title", ""),
                path=_with_part(c, labels),
                level=c.get("level", 0),
                label=c.get("label", ""), text=text,
                page=c.get("page") or 0, block_index=c.get("block_index") or 0,
                char_offset=c.get("char_offset") or 0,
                start=c.get("start") or 0, end=c.get("end") or 0,
                status=st, effective_from=d.get("effective_from"),
                effective_to=d.get("effective_to"),
                url=d.get("url", ""), parser_version=d.get("parser_version", ""),
                carrier=c.get("carrier") or "page", extract=d.get("extract") or "native",
                part=c.get("part") or 0,
                sec=c.get("sec") or 0,
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
        paths = [(c.part, tuple(c.path)) for c in doc]
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
    # 判"那一年是否有"只需要起止日期，不必回读 574 份全文
    _meta = {}
    for f in sorted(PROCESSED.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        if d.get("doc_no"):
            _meta[d["doc_no"]] = {"doc_no": d["doc_no"],
                                  "effective_from": d.get("effective_from"),
                                  "effective_to": d.get("effective_to"),
                                  # 成文日期：条文不可能在成文之前生效，所以它可以当**下界**用
                                  "issued_at": d.get("issued_at")}
    (INDEX / "docs_meta.json").write_text(json.dumps(_meta, ensure_ascii=False), "utf-8")
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
