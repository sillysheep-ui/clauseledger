"""有效期链：把「延长有效期通知」里的新到期日，回填到被延长的文件上。

为什么要单独做一层：文件自己写的「有效期至 X」只是**它自己的声明**。
一份文件可能被后来的延期通知继续延长，也可能被废止通知提前终止。
只读文件自身的声明，会把 2018 年发布、2020 年到期、后来被延到 2031 年的文件判成"已过期"。

这一层只做**延期**（延长/延续有效期），不做废止：
  - 延期通知的形态很固定：标题含「延长…有效期」，正文含「有效期延长至 YYYY年M月D日」，
    后面跟一张表列出被延长的文件（文号 + 标题）；
  - 表格在 PDF 里会被拍平，文号可能丢（实测沪人社规〔2021〕26号 就丢了），
    所以文号匹配之外还要用**标题子串**兜底。

产出（写回 data/processed/*.json，不改证据字段）：
  - effective_to      保留：文件自己的声明，是证据
  - valid_until       派生：取「自身到期」与「所有延期日期」的最大值
  - extended_by       派生：延长过它的文号列表
  - validity_source   document / extension / unknown
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PROCESSED = Path("data/processed")
REPORT = Path("docs/scan/VALIDITY.md")

RE_DOCNO = re.compile(
    r"沪[\u4e00-\u9fa5]{1,8}[〔（(]\s*\d{2,4}\s*[〕）)]\s*第?\s*\d+\s*号"
)
RE_EXT_DATE = re.compile(
    r"(?:有效期\s*(?:延长|延至|续期)?\s*至|延长至|延至|续期至)"
    r"\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)


def is_extension(doc: dict) -> bool:
    """只认「延长部分行政规范性文件有效期」这一类通知。

    「延续实施某项政策」不算：那是新政策，不是延长旧文件。
    """
    title = doc.get("title") or ""
    return "延长" in title and "有效期" in title


def key(no: str) -> str:
    """文号归一：去空格、统一括号。OCR/网页里 `〔2018〕 20号` 与 `〔2018〕20号` 是同一个。"""
    t = re.sub(r"\s+", "", no or "")
    return t.replace("（", "〔").replace("）", "〕").replace("(", "〔").replace(")", "〕")


def norm(s: str) -> str:
    return re.sub(r"[\s《》〈〉〔〕【】（）()、，,。.:：;；]", "", s or "")


def body_text(doc: dict) -> str:
    return "\n".join((b.get("text") or "") for b in doc.get("blocks", []))


def dates_in(doc: dict) -> list[str]:
    """通知里出现的所有新到期日（去重、排序）。"""
    out = {f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
           for y, m, d in RE_EXT_DATE.findall(body_text(doc))}
    return sorted(out)


def end_date(doc: dict) -> str:
    """通知里统一的新到期日。

    只有「所有列出的文件延到同一天」才返回日期；多日期表格返回空串，
    交给上层标成未解析——把第一条日期套给所有文件，是比不知道更糟的错误。
    """
    ds = dates_in(doc)
    return ds[0] if len(ds) == 1 else ""


def row_pairs(doc: dict) -> list[tuple[str, str]]:
    """按行配对「文号 → 新到期日」。

    只对**行优先**的表格有效（文本形如 `…46 号 | 有效期至2022年 12月31日`）。
    列优先的扫描件（先所有标题、再所有文号、再所有日期）在这里配不出来，
    配对距离超限就放弃——宁可标未解析，也不要给文件安一个别人的日期。
    """
    text = body_text(doc)
    out: list[tuple[str, str]] = []
    ambiguous = False
    for m in RE_DOCNO.finditer(text):
        tail = text[m.end(): m.end() + 90]
        dm = RE_EXT_DATE.search(tail)
        if not dm:
            continue
        # 文号与日期之间又出现了别的文号 → 这是"文号一列、日期一列"的列优先排版，
        # 逐个配对必然错位。整份放弃，交给人工。
        if RE_DOCNO.search(tail[: dm.start()]):
            ambiguous = True
            break
        y, mo, d = (int(x) for x in dm.groups())
        out.append((m.group(0), f"{y:04d}-{mo:02d}-{d:02d}"))
    return [] if ambiguous else out


def targets(notice: dict, docs: dict[str, dict]) -> list[str]:
    """通知里被延长的文号。

    两条路并用：正文里的文号（精确）+ 已知标题的子串匹配（兜底，PDF 拍平会丢文号）。
    """
    text = body_text(notice)
    own = notice.get("doc_no")
    found: list[str] = []

    for no in RE_DOCNO.findall(text):
        if no != own and no in docs:
            found.append(no)

    flat = norm(text)
    for no, doc in docs.items():
        if no == own or no in found:
            continue
        t = norm(doc.get("title") or "")
        if len(t) >= 12 and t in flat:
            found.append(no)
    return sorted(set(found))


@dataclass
class Edge:
    by: str
    to: str
    until: str


@dataclass
class Resolution:
    valid_until: dict[str, str] = field(default_factory=dict)
    extended_by: dict[str, list[str]] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def build(docs: dict[str, dict]) -> Resolution:
    res = Resolution()
    bykey = {key(n): n for n in docs}
    for no, doc in docs.items():
        if not is_extension(doc):
            continue
        until = end_date(doc)
        if until:
            tg = [bykey[key(t)] for t in targets(doc, docs) if key(t) in bykey]
            if not tg:
                res.unresolved.append(f"{no}（延期通知里没认出被延长的文件）")
                continue
            for t in tg:
                res.edges.append(Edge(by=no, to=t, until=until))
                res.extended_by.setdefault(t, []).append(no)
                res.valid_until[t] = max(res.valid_until.get(t, ""), until)
            continue

        # 多日期：先试按行配对
        pairs = [(bykey[key(t)], u) for t, u in row_pairs(doc)
                 if key(t) in bykey and bykey[key(t)] != no]
        if pairs:
            for t, u in pairs:
                res.edges.append(Edge(by=no, to=t, until=u))
                res.extended_by.setdefault(t, []).append(no)
                res.valid_until[t] = max(res.valid_until.get(t, ""), u)
            continue
        if not dates_in(doc):
            res.unresolved.append(f"{no}（没找到日期）")
        elif not targets(doc, docs):
            res.unresolved.append(f"{no}（延期通知里没认出被延长的文件）")
        else:
            res.unresolved.append(f"{no}（多日期表格，列优先，无法按行配对）")
    return res


def own_end(doc: dict) -> str:
    """文件**自己**声明的到期日。

    绝不能回退到 valid_until：那是派生字段，上一轮写进去的值如果被当成本次输入，
    延期边一旦消失（比如解析规则收紧），旧值就会永远留在记录里，成为无法追溯的脏数据。
    """
    return (doc.get("effective_to") or "")[:10]


def apply(res: Resolution, docs: dict[str, dict]) -> dict[str, int]:
    """把派生字段写回处理结果。幂等：重复跑结果不变。"""
    stats = {"extended": 0, "unchanged": 0, "no_end": 0}
    for no, doc in docs.items():
        own = own_end(doc)
        ext = res.valid_until.get(no, "")
        if ext and ext > own:
            doc["valid_until"] = ext
            doc["extended_by"] = res.extended_by.get(no, [])
            doc["validity_source"] = "extension"
            stats["extended"] += 1
        elif own:
            doc["valid_until"] = own
            doc["extended_by"] = []
            doc["validity_source"] = "document"
            stats["unchanged"] += 1
        else:
            doc["valid_until"] = ""
            doc["extended_by"] = []
            doc["validity_source"] = "unknown"
            stats["no_end"] += 1
    return stats


def load() -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for f in sorted(PROCESSED.glob("*.json")):
        d = json.loads(f.read_text("utf-8"))
        if d.get("doc_no"):
            docs[d["doc_no"]] = d
    return docs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 data/processed/*.json")
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args()

    docs = load()
    res = build(docs)
    stats = apply(res, docs)

    print(f"文件 {len(docs)} 份；延期通知 {sum(1 for d in docs.values() if is_extension(d))} 份")
    print(f"延期边 {len(res.edges)} 条；被延长 {len(res.valid_until)} 份")
    print("统计：", stats)
    for u in res.unresolved:
        print("  未解析：", u)
    for e in res.edges[:20]:
        print(f"  {e.to} ← {e.by}（延至 {e.until}）")

    if args.apply:
        for f in sorted(PROCESSED.glob("*.json")):
            d = json.loads(f.read_text("utf-8"))
            no = d.get("doc_no")
            if no in docs:
                d.update({k: docs[no][k] for k in ("valid_until", "extended_by", "validity_source")})
                f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        print("已写回 processed")


if __name__ == "__main__":
    main()
