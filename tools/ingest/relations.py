"""关系抽取：从条款正文里抽出"谁废止了谁""谁又延了谁"。

为什么单独一层：文件自身的字段（文号、有效期）写在它自己身上，
但"被废止"这件事写在**别人的文件**里。只抽自身字段，`repealed_by` 永远是空的，
于是 `沪人社福发〔2015〕34号` 的有效期是空、看起来仍有效——实测这一条错让 9 道题判错。

产物是事件表，不是知识图谱：
    {"type":"repeal","source":"沪人社规〔2020〕26号","target":"沪人社福发〔2015〕34号",
     "effective_date":"2020-12-01","evidence":{"clause":"九、","text":"…同时废止…"}}

用法：python -m tools.ingest.relations [--out data/registry/relations.jsonl]
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path("data/registry/relations.jsonl")
REPORT = Path("data/reports/relations.md")

# 目标文号：沪人社规〔2020〕26号 / 沪劳资发(87)130号 / 国发〔2021〕7号
RE_NO = re.compile(r"[\u4e00-\u9fa5]{1,8}\s*[〔（(\[]\s*\d{2,4}\s*[〕］）)\]]\s*第?\s*\d+\s*号")
# 目标标题：《上海市…办法》
RE_TITLE = re.compile(r"《([^》\n]{4,80})》")
REPEAL = re.compile(r"(废止|停止执行|不再执行|予以失效)")
EXTEND = re.compile(r"(有效期|期限)[^。；]{0,20}(延长|延至|延续)[^。；]{0,20}(至)?\s*"
                    r"([12]\d{3}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)")
DATE = re.compile(r"([12]\d{3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def iso(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def flat(s: str) -> str:
    return re.sub(r"[\s　〔〕［］（）()\[\]]", "", s or "")


def norm_title(s: str) -> str:
    s = re.sub(r"^上海市?", "", s or "")
    return re.sub(r"[\s　《》〔〕（）()、，,。：:·—\-“”\"']", "", s)


def load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in glob.glob("data/processed/*.json"):
        d = json.load(open(p))
        if d.get("doc_no"):
            out[d["doc_no"]] = d
    return out


def doc_date(d: dict) -> str | None:
    """废止生效日：优先用源文件的施行日（自 X 日起施行），其次发文日。"""
    return d.get("effective_from") or d.get("issued_at")


def units(d: dict) -> list[dict]:
    """切分单元：有条款用条款；表格型文件（0 条款）退回用 table 块。"""
    cls = [c for c in (d.get("clauses") or []) if (c.get("text") or "").strip()]
    if cls:
        return cls
    return [{"path": f"table#{b['i']}", "page": b.get("page"), "text": b.get("text") or ""}
            for b in (d.get("blocks") or []) if b.get("kind") == "table"]


SENT = re.compile(r"[^。；！？\n]{1,400}[。；！？]?")


def sentences(text: str) -> list[str]:
    return [x.strip() for x in SENT.findall(text) if x.strip()]


def build(docs: dict[str, dict]) -> list[dict]:
    by_title = defaultdict(list)
    for no, d in docs.items():
        by_title[norm_title(d.get("title") or "")].append(no)
    by_flat = {flat(no): no for no in docs}

    edges: list[dict] = []
    for no, d in docs.items():
        default_date = doc_date(d)
        for c in units(d):
            sents = sentences(c.get("text") or "")
            for si, sent in enumerate(sents):
                kind = None
                if REPEAL.search(sent):
                    # "第X条…停止执行" 只废掉一部分，不能当整份废止
                    kind = "partial_repeal" if re.search(r"第[一二三四五六七八九十百\d]{1,4}条", sent) else "repeal"
                elif EXTEND.search(sent):
                    kind = "extend"
                if not kind:
                    continue
                # 列表式："同时废止下列文件：" 后面一串 —— 往后看两句
                block = [sent]
                if kind == "repeal" and not RE_NO.search(sent) and not RE_TITLE.search(sent):
                    block += sents[si + 1: si + 3]
                win = " ".join(block)

                eff = None
                m_d = re.search(r"自\s*([12]\d{3}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*起", sent)
                if m_d:
                    g = DATE.search(m_d.group(1))
                    if g:
                        eff = iso(*g.groups())
                if not eff and kind == "extend":
                    g = DATE.search(sent)
                    if g:
                        eff = iso(*g.groups())
                if not eff and kind in ("repeal", "partial_repeal"):
                    eff = default_date

                def emit(target, raw, title, conf):
                    if target == no:
                        return
                    edges.append({
                        "type": kind, "source": no, "target": target,
                        "target_raw": raw, "target_title": title,
                        "effective_date": eff, "date_guess": eff is None,
                        "confidence": conf,
                        "evidence": {"path": c.get("path"), "page": c.get("page"),
                                     "text": sent[:300]},
                    })

                nos = [re.sub(r"\s+", "", r) for r in RE_NO.findall(win)]
                if nos:                      # 有文号就只用文号，避免同名文件串味
                    for raw in nos:
                        tgt = by_flat.get(flat(raw))
                        emit(tgt, raw, None, "exact" if tgt else "unresolved")
                    continue
                for raw in RE_TITLE.findall(win):
                    key = norm_title(raw)
                    if not key or len(key) < 6:
                        continue
                    tgts = by_title.get(key) or []
                    if not tgts:
                        tgts = [n for k, ns in by_title.items()
                                if k and (key in k or k in key) for n in ns]
                    if not tgts:
                        emit(None, None, raw, "unresolved")
                    for t in tgts:
                        emit(t, None, raw, "title")

    seen = {}
    for e in edges:
        k = (e["type"], e["source"], e["target"] or e["target_title"], e["effective_date"])
        seen.setdefault(k, e)
    return list(seen.values())


def report(edges: list[dict], docs: dict[str, dict]) -> str:
    by_type = Counter(e["type"] for e in edges)
    conf = Counter(e["confidence"] for e in edges)
    resolved = [e for e in edges if e["target"]]
    lines = [
        "# 关系抽取（废止 / 延期）", "",
        f"- 事件总数：**{len(edges)}**",
        f"- 类型：{dict(by_type)}",
        f"- 置信度：{dict(conf)}",
        f"- 指向库内文件：**{len(resolved)}**；库外或未解析：{len(edges) - len(resolved)}",
        "",
        "## 样本（20 条，供人工核对）", "",
        "| 类型 | 源文件 | 目标 | 生效日 | 证据 |",
        "|---|---|---|---|---|",
    ]
    for e in edges[:20]:
        tgt = e["target"] or (e["target_title"] or e["target_raw"] or "?")
        ev = (e["evidence"]["text"] or "")[:40].replace("|", "／")
        lines.append(f"| {e['type']} | {e['source']} | {tgt} | {e['effective_date'] or '未定'} | {ev} |")
    zero = [e for e in edges if not e["effective_date"]]
    lines += ["", f"## 生效日缺失：{len(zero)} 条", ""]
    for e in zero[:10]:
        lines.append(f"- {e['source']} → {e['target'] or e['target_title']}：`date_unknown`")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    docs = load()
    edges = build(docs)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in edges), "utf-8")
    md = report(edges, docs)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(md, "utf-8")
    print(md)


if __name__ == "__main__":
    main()
