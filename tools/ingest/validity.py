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
import glob
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PROCESSED = Path("data/processed")
REPORT = Path("docs/scan/VALIDITY.md")

# 空格必须容忍：HTML 表格是「沪人社福发 〔 2014 〕 36 号」，
# OCR 是「沪人社规〔2020〕9 号」。早期版本不允许空格，14 行的表格因此整份配不出来。
# 老文号里「（86）资创字第225号」这种中间还有字的形态很常见（扫描件、
# 延期清单里都有），所以〔年份〕与「第…号」之间允许最多 6 个汉字。
RE_DOCNO = re.compile(
    r"沪\s*[\u4e00-\u9fa5]{0,10}\s*[〔（(]\s*\d{2,4}\s*[〕）)]\s*[\u4e00-\u9fa5]{0,6}\s*第?\s*\d+\s*号"
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


from app.domain.docno import key  # noqa: E402  文号归一只有一份实现


def norm(s: str) -> str:
    return re.sub(r"[\s《》〈〉〔〕【】（）()、，,。.:：;；]", "", s or "")


def body_text(doc: dict) -> str:
    return "\n".join((b.get("text") or "") for b in doc.get("blocks", []))


def dates_in(doc: dict) -> list[str]:
    """通知里出现的所有新到期日（去重、排序）。"""
    out = {f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
           for y, m, d in RE_EXT_DATE.findall(body_text(doc))}
    return sorted(out)


def date_list(doc: dict) -> list[str]:
    """按出现顺序列日期，**保留重复**——列优先表格靠"第几个日期"对齐。"""
    return [f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            for y, m, d in RE_EXT_DATE.findall(body_text(doc))]


def align_by_count(doc: dict, docs: dict[str, dict], own: str | None = None) -> list[tuple[str, str]]:
    """列优先排版：文号一列、日期一列，两者按出现顺序一一对应。

    只在**数量完全相等**且文号互不重复时配对。数量不等就说明有单元格丢了
    （OCR 常把长文号截断），此时按顺序硬配会让第 3 个文件拿到第 4 个日期——
    比不知道更糟，所以直接返回空。
    """
    text = body_text(doc)
    # 通知自己的文号也出现在版头，先剔掉；否则数量永远差一个，永远对不齐。
    nos = [m.group(0) for m in RE_DOCNO.finditer(text) if key(m.group(0)) != key(own or "")]
    ds = date_list(doc)
    if len(nos) < 2 or len(nos) != len(ds):
        return []
    if len({key(n) for n in nos}) != len(nos):
        return []
    return list(zip(nos, ds))


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

    if not found:
        found.extend(_by_quoted_title(text, own, docs))
    return sorted(set(found))


def _by_quoted_title(text: str, own: str | None, docs: dict[str, dict]) -> list[str]:
    """单文件延期：正文只写《…》标题、不写文号时，按标题相似度找目标。

    通知标题本身也含目标标题，所以只取**书名号里的**片段，并要求最佳匹配
    明显高于次佳（差值 ≥ 0.05），否则宁可不认。
    """
    import difflib
    quoted = {norm(q) for q in re.findall(r"《([^》]{6,90})》", text)}
    if not quoted:
        return []
    scored: list[tuple[float, str]] = []
    for no, doc in docs.items():
        if no == own:
            continue
        t = norm(doc.get("title") or "")
        if len(t) < 12:
            continue
        best = max((difflib.SequenceMatcher(None, q, t).ratio() for q in quoted), default=0.0)
        if best >= 0.86:
            scored.append((best, no))
    if not scored:
        return []
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
        return []
    return [scored[0][1]]


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


RAW = Path("data/raw")
RELATIONS = Path("data/registry/relations.jsonl")


def load_repeals(path: Path = RELATIONS) -> dict[str, list[dict]]:
    """废止关系（由 tools/ingest/relations.py 从条款正文抽出来）。

    它和延期链是同一件事的两面：都是"别的文件改变了这份文件的有效期"。
    所以最后都要落进同一张时间表，而不是一个查关系表、一个查单值字段。
    """
    out: dict[str, list[dict]] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") not in ("repeal", "partial_repeal"):
            continue
        target, date = r.get("target"), r.get("effective_date")
        if not target or not date:
            continue
        out.setdefault(target, []).append(
            {"by": r["source"], "date": str(date)[:10], "kind": r["type"]})
    return out


def _pdf_path(doc: dict) -> str | None:
    for a in doc.get("attachments") or []:
        if a.get("kind") != "pdf" or not a.get("sha256"):
            continue
        hits = sorted(glob.glob(str(RAW / a["sha256"][:12] / "*")))
        if hits:
            return hits[0]
    return None


def table_pairs(doc: dict) -> list[tuple[str, str]]:
    """从表格的「文号列 / 有效期列」按顺序配对（只用于补齐行内配对的缺口）。"""
    for t in doc.get("tables", []):
        rows = t.get("rows") or []
        if len(rows) < 2:
            continue
        heads = [str(h) for h in (t.get("header") or rows[0])]
        no_col = next((i for i, h in enumerate(heads) if "文号" in h), None)
        dt_col = next((i for i, h in enumerate(heads) if "有效期" in h or "期限" in h), None)
        if no_col is None or dt_col is None or no_col == dt_col:
            continue
        nos = [r[no_col].strip() for r in rows[1:] if no_col < len(r) and RE_DOCNO.search(r[no_col])]
        dts = [r[dt_col].strip() for r in rows[1:] if dt_col < len(r) and RE_EXT_DATE.search(r[dt_col])]
        if not nos or len(nos) != len(dts) or len(set(nos)) != len(nos):
            continue
        out = []
        for n, dd in zip(nos, dts):
            m = RE_EXT_DATE.search(dd)
            if not m:
                break
            y, mo, d2 = (int(x) for x in m.groups())
            out.append((RE_DOCNO.search(n).group(0), f"{y:04d}-{mo:02d}-{d2:02d}"))
        if len(out) == len(nos):
            return out
    return []


def _vision_pairs(doc: dict) -> list[tuple[str, str]]:
    """扫描件兜底：用 Vision 的坐标按列重建单元格，再按行配对。"""
    try:
        from tools.ingest import vision
    except Exception:  # pragma: no cover
        return []
    path = _pdf_path(doc)
    return vision.row_pairs(path) if path else []


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
                reason = ("被延长的文件不在语料里"
                          if re.search(r"《[^》]{6,90}》", body_text(doc))
                          else "延期通知里没认出被延长的文件")
                res.unresolved.append(f"{no}（{reason}）")
                continue
            for t in tg:
                res.edges.append(Edge(by=no, to=t, until=until))
                res.extended_by.setdefault(t, []).append(no)
                res.valid_until[t] = max(res.valid_until.get(t, ""), until)
            continue

        # 多日期：三种读法取并集（行内相邻 → 列优先按数量 → 表格列按顺序），
        # 任何一种读法漏掉的条目都还能被另一种补回来；键去重，先到先得。
        raw = row_pairs(doc)
        seen = {key(t) for t, _ in raw}
        for src in (align_by_count(doc, docs, no), table_pairs(doc), _vision_pairs(doc)):
            for t, u in src:
                if key(t) not in seen:
                    raw.append((t, u)); seen.add(key(t))
        pairs = [(bykey[key(t)], u) for t, u in raw
                 if key(t) in bykey and bykey[key(t)] != no]
        if pairs:
            for t, u in pairs:
                res.edges.append(Edge(by=no, to=t, until=u))
                res.extended_by.setdefault(t, []).append(no)
                res.valid_until[t] = max(res.valid_until.get(t, ""), u)
            continue
        vis = [(bykey[key(t)], u) for t, u in _vision_pairs(doc)
               if key(t) in bykey and bykey[key(t)] != no]
        if vis:
            for t, u in vis:
                res.edges.append(Edge(by=no, to=t, until=u))
                res.extended_by.setdefault(t, []).append(no)
                res.valid_until[t] = max(res.valid_until.get(t, ""), u)
            continue
        if not dates_in(doc):
            res.unresolved.append(f"{no}（没找到日期）")
        elif not targets(doc, docs):
            if re.search(r"《[^》]{6,90}》", body_text(doc)):
                # 正文只写了被延长文件的标题，标题在语料里找不到——不是解析失败，
                # 是**被延长的那个文件本身不在语料**（多半是早年已废止、未抓取）。
                res.unresolved.append(f"{no}（被延长的文件不在语料里）")
            else:
                res.unresolved.append(f"{no}（延期通知里没认出被延长的文件）")
        else:
            res.unresolved.append(f"{no}（列优先：文号与日期数量不齐，拒绝硬配）")
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
        print("派生：", write_derived(res, docs))
        for f in sorted(PROCESSED.glob("*.json")):
            d = json.loads(f.read_text("utf-8"))
            no = d.get("doc_no")
            if no in docs:
                d.update({k: docs[no][k] for k in ("valid_until", "extended_by", "validity_source")})
                f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        print("已写回 processed")



# ---------------------------------------------------------------- 时间段
#
# 一个 `valid_until` 字段回答不了「2021 年 5 月那天它有效吗」：
# 文件可能 2020 年到期、2026 年才被一份新的通知延到 2031 年，
# 中间那六年是**断的**。把有效期存成单值，等于默认"断档不存在"。


@dataclass
class Period:
    start: str
    end: str            # "" = 未记载到期
    by: list[str] = field(default_factory=list)
    by_on: list[str] = field(default_factory=list)   # 对应通知的生效日
    gap: bool = False   # 这一段与上一段之间不连续
    # 段内的"步"：文件自身一个终点，每次延期再一步。
    # 有了它，才能回答"2022 年问它，到期日是多少"——而不是拿合并后的最后终点。
    steps: list[dict] = field(default_factory=list)


def _notice_date(no: str, docs: dict[str, dict]) -> str:
    d = docs.get(no) or {}
    return str(d.get("effective_from") or d.get("issued_at") or "")[:10]


def periods(res: Resolution, docs: dict[str, dict],
            repeals: dict[str, list[dict]] | None = None) -> dict[str, list[Period]]:
    """按时间顺序拼出每份文件的有效期区间。

    连续延长（通知生效日 ≤ 当前到期日）就把当前段往后推；
    否则新开一段并标 `gap=True`——断档是事实，不该被抹平。
    """
    out: dict[str, list[Period]] = {}
    for no, doc in docs.items():
        start = str(doc.get("effective_from") or doc.get("issued_at") or "")[:10]
        own = own_end(doc)
        if not start and not own and no not in res.valid_until:
            continue
        segs = [Period(start=start, end=own, steps=[{"end": own, "on": start}])]
        mine = sorted((e for e in res.edges if e.to == no),
                      key=lambda e: (_notice_date(e.by, docs), e.until))
        for e in mine:
            nd = _notice_date(e.by, docs)
            cur = segs[-1]
            if not cur.end or (nd and nd <= cur.end):
                if e.until > (cur.end or ""):
                    cur.end = e.until
                if e.by not in cur.by:
                    cur.by.append(e.by)
                    cur.by_on.append(nd)
                    cur.steps.append({"end": e.until, "on": nd, "by": e.by})
            else:
                segs.append(Period(start=nd or cur.end, end=e.until, by=[e.by],
                                   by_on=[nd], gap=True,
                                   steps=[{"end": e.until, "on": nd, "by": e.by}]))
        # 废止也是时间表的一部分：在被废止那天画一条终点线，
        # 这样"某年有效吗""按 as_of 检索"和卡片上的时效提醒用的是同一套数据。
        rep = sorted((repeals or {}).get(no, []), key=lambda r: r["date"])
        if rep:
            cut = rep[0]["date"]
            cur = segs[-1]
            if cut >= (cur.start or cut) and (not cur.end or cut < cur.end):
                cur.end = cut
        out[no] = segs
    return out


def write_derived(res: Resolution, docs: dict[str, dict],
                  path: Path = Path("data/derived/validity.json"),
                  repeals: dict[str, list[dict]] | None = None) -> dict:
    """把时间段写成派生文件。processed/ 里留的是"文件自己说的"，这里是"算出来的"。"""
    repeals = repeals if repeals is not None else load_repeals()
    segs = periods(res, docs, repeals)
    periods_out: dict[str, list[dict]] = {}
    alias: dict[str, str] = {}
    for no, ps in segs.items():
        k = key(no)
        periods_out[k] = [{"start": p.start, "end": p.end, "by": p.by,
                           "by_on": p.by_on, "gap": p.gap, "steps": p.steps,
                           "doc_no": no} for p in ps]
        if k != no:
            alias[no] = k
    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "schema": 2,
        # 键是匹配键（两位年份已补成四位、括号已统一），展示用 entry 里的 doc_no
        "periods": periods_out,
        "alias": alias,
        "edges": [{"by": e.by, "to": e.to, "until": e.until} for e in res.edges],
        "repeals": repeals,
        "unresolved": res.unresolved,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    return {"periods": len(payload["periods"]), "edges": len(payload["edges"]),
            "gaps": sum(1 for ps in payload["periods"].values() for p in ps if p["gap"])}


if __name__ == "__main__":
    main()
