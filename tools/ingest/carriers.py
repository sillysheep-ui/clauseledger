"""载体：同一份文件的多个副本，选一个主副本；附件不是副本的，按补充内容保留。

为什么要有这一层——一开始我们把「页面正文」和「附件」当成两份内容拼在一起，
结果同一句话在库里出现两次：一次来自网页（没有页码），一次来自 PDF（第 3 页）。
下游全部为这个错误付利息：条款数虚高、检索名额被同句占掉、引用坐标时有时无。

但也不能简单"只留一个"：实测 296 个载体对里，180 对是重复（Jaccard ≥0.6），
106 对几乎不重叠——那些是附件里的实施细则、名单、表格，是**补充内容**不是副本。
所以分三类：主副本（提供正文与坐标）、补充（去重后并入）、副本（只登记字节）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tools.ingest.clause import Clause, split_clauses
from tools.ingest.parse import PARSER_VERSION, Block, parse_any
from tools.ingest.pipeline import _raw_file, drop_nav

SHORT_RATIO = 0.60      # 字数不到最全载体的 60% 视为"明显不全"
OCR_TRUST = 0.85        # 扫描件置信度达到这个值才敢当主副本
MIN_CHARS = 40          # 低于这个字数基本是空壳
DUP_JACCARD = 0.60      # 路径重合度达到这个值判为副本
SUP_CHARS = 400         # 补充内容的最少字数
SUP_CLAUSES = 2         # 补充内容的最少条款数（1 条的多半是表单）


@dataclass
class Carrier:
    role: str                 # page / attachment
    idx: int                  # page=0；附件从 1 开始
    sha256: str
    path: Path | None
    url: str
    kind: str = ""            # html / pdf / docx / doc
    extract: str = "native"   # native / ocr / textutil / unsupported
    ocr_conf: float | None = None
    blocks: list[Block] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)
    tables: list = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)

    @property
    def label(self) -> str:
        return "page" if self.role == "page" else f"att{self.idx}"

    @property
    def human(self) -> str:
        return "页面" if self.role == "page" else f"附件{self.idx}"

    def summary(self) -> dict:
        return {
            "role": self.role, "idx": self.idx, "kind": self.kind,
            "extract": self.extract, "ocr_conf": self.ocr_conf,
            "chars": self.chars, "blocks": len(self.blocks),
            "clauses": len(self.clauses), "sha256": self.sha256, "url": self.url,
        }


def load_carriers(rec: dict) -> list[Carrier]:
    """把一条记录拆成若干载体，每个单独解析（不合并）。"""
    out: list[Carrier] = []
    page_file = _raw_file(rec.get("page_sha256"))
    if page_file is not None:
        url = rec.get("url") or ""
        try:
            kind, parsed = parse_any(page_file.read_bytes(), "", url, str(page_file))
            out.append(Carrier(
                role="page", idx=0, sha256=rec.get("page_sha256") or "",
                path=page_file, url=url, kind=kind, extract=parsed.extract,
                ocr_conf=parsed.ocr_conf,
                blocks=[Block(i, b.kind, b.text, b.page) for i, b in enumerate(parsed.blocks)],
                tables=list(parsed.tables),
            ))
        except Exception:
            pass
    for i, a in enumerate(rec.get("attachments") or [], start=1):
        sha = a.get("sha256")
        af = _raw_file(sha)
        if af is None:
            continue
        try:
            kind, parsed = parse_any(af.read_bytes(), "", a.get("url") or "", str(af))
        except Exception:
            continue
        out.append(Carrier(
            role="attachment", idx=i, sha256=sha or "", path=af,
            url=a.get("url") or "", kind=kind, extract=parsed.extract,
            ocr_conf=parsed.ocr_conf,
            blocks=[Block(j, b.kind, b.text, b.page) for j, b in enumerate(parsed.blocks)],
            tables=list(parsed.tables),
        ))
    for c in out:
        c.blocks = drop_nav(c.blocks)
        c.clauses = split_clauses(c.blocks, rec.get("doc_no") or "")
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def choose_main(cands: list[Carrier]) -> tuple[Carrier | None, str]:
    usable = [c for c in cands if c.chars >= MIN_CHARS]
    if not usable:
        return None, "所有载体都没有正文"
    fullest = max(c.chars for c in usable)
    if len(usable) > 1:
        keep = [c for c in usable if c.chars >= SHORT_RATIO * fullest] or usable
        skipped = [c.human for c in usable if c not in keep]
        usable = keep
        note = f"；排除明显不全的{'、'.join(skipped)}" if skipped else ""
    else:
        note = ""

    for c in usable:
        if c.kind == "pdf" and c.extract == "native":
            return c, "原生 PDF 文本层：文字准确且带页码" + note
    for c in usable:
        if c.extract == "ocr" and (c.ocr_conf or 0) >= OCR_TRUST:
            return c, f"扫描件可识别（置信度 {c.ocr_conf}）且带页码" + note
    for c in usable:
        if c.role == "page" and c.extract == "native":
            return c, "网页正文可直接读，不需要 OCR" + note
    c = max(usable, key=lambda x: (len(x.clauses), x.chars))
    return c, f"其余规则不适用，取条款最多的一份（{len(c.clauses)} 条，{c.chars} 字）" + note


@dataclass
class Plan:
    main: Carrier | None
    supplements: list[tuple[Carrier, bool]]   # (载体, 是否只取新路径)
    dropped: list[tuple[Carrier, str]]
    reason: str


def plan(rec: dict, cands: list[Carrier]) -> Plan:
    main, why = choose_main(cands)
    if main is None:
        return Plan(None, [], [(c, "无正文") for c in cands], why)
    pset = {(c.part, c.path) for c in main.clauses}
    sups: list[tuple[Carrier, bool]] = []
    drops: list[tuple[Carrier, str]] = []
    for c in cands:
        if c is main:
            continue
        j = _jaccard({p for _, p in pset}, {x.path for x in c.clauses})
        if j >= DUP_JACCARD:
            # 重合度高不等于完全一样：网页版常多出通知抬头、附则、附件说明。
            # 只把"主副本没有的路径"补进来，既不重复也不丢内容。
            extra = {x.path for x in c.clauses} - {p for _, p in pset}
            if extra and c.chars >= SUP_CHARS:
                sups.append((c, True))
                continue
            drops.append((c, f"与主副本重合 {j:.2f}，判为副本"))
        elif c.chars >= SUP_CHARS and len(c.clauses) >= SUP_CLAUSES:
            sups.append((c, False))
        else:
            drops.append((c, "内容过少或为表单，不并入"))
    if sups:
        why += f"；并入补充内容 {'、'.join(c.human for c, _ in sups)}"
    return Plan(main, sups, drops, why)


def relocate(blocks: list[Block], clauses: list[Clause]) -> None:
    """按**块锚点**重算偏移，保证 start 单调、父子区间自洽。

    不能用 text.find 顺序搜索：同一份文件里"三、"可能在正文与附件各出现一次，
    搜索会把父条款落到后一份、子条款落到前一份，父子区间就互相打架
    （2026-11 实测 17 份文档 I2/I3 违规）。
    """
    starts, cur = [], 0
    for b in blocks:
        starts.append(cur)
        cur += len(b.text) + 1
    text = "\n".join(b.text for b in blocks)
    for c in clauses:
        i = -1
        bi = c.block_index
        if 0 <= bi < len(blocks):
            off = c.char_offset
            # 只信块锚点，不做逐字比对：父条款常常横跨多个块，
            # 拿"一个块内的切片"去比必然失败，然后回落到文本搜索又把位置找错
            if 0 <= off <= len(blocks[bi].text):
                i = starts[bi] + off
        if i < 0:
            i = text.find(c.text)
        if i < 0:
            continue
        c.start, c.end = i, i + len(c.text)


def _dates(text: str, rec: dict) -> dict:
    """到期日：正文优先，其次列表页（来源要分清）。

    正文说的是"文件自己写的那天"，列表页的"有效期至"是**当前**效力——可能已经被延期。
    所以 own_effective_to 只装正文日期；effective_to 在正文缺失时退回列表页日期，
    并标 source=list，让 steps 知道这一步不是文件自己的声明。
    """
    from tools.ingest.pipeline import RE_EFF_TO, iso  # 局部导入，避免循环依赖

    m = RE_EFF_TO.search(text or "")
    body = iso(m.group(1)) if m else None
    if body:
        return {"effective_to": body, "own_effective_to": body,
                "effective_to_source": "document"}
    listed = (rec.get("list_valid_until") or rec.get("valid_until") or "")[:10]
    return {"effective_to": listed, "own_effective_to": "",
            "effective_to_source": "list" if listed else "unknown"}


def rebuild(rec: dict) -> dict:
    """主副本提供正文与坐标；补充内容去重后追加；副本只登记。"""
    cands = load_carriers(rec)
    p = plan(rec, cands)
    if p.main is None:
        return {**rec, "carriers": [c.summary() for c in cands],
                "carrier_choice": {"role": None, "reason": p.reason}}

    # 主副本"明显小于附件自己声明的字数"= 解析失败被当成成功，宁可报错也不写坏记录。
    declared = max([a.get("chars") or 0 for a in (rec.get("attachments") or [])] + [0])
    got = sum(len(b.text) for b in p.main.blocks)
    if declared >= 1000 and got < 0.5 * declared:
        raise RuntimeError(
            f"主副本疑似解析失败：{p.main.human} 只有 {got} 字，附件声明 {declared} 字")
    blocks = [Block(i, b.kind, b.text, b.page) for i, b in enumerate(p.main.blocks)]
    base = sum(len(b.text) for b in blocks) + 1
    seen = {(c.path, _norm(c.text)) for c in p.main.clauses}
    clauses = []
    for c in p.main.clauses:
        c.carrier = p.main.label
        clauses.append(c)
    main_paths = {tuple(c.path) for c in p.main.clauses}
    for sup, partial in p.supplements:
        # partial=True 表示该载体与主副本大量重叠，只取主副本没有的路径；
        # 这里以前写的是 `{p for _, p in pset}`，pset 从未定义——
        # 一旦真有"部分重叠"的载体就会 NameError（语料里恰好没触发，属于躲过去的 bug）。
        off = len(blocks)
        for b in sup.blocks:
            blocks.append(Block(len(blocks), b.kind, b.text, b.page))
        sbase = base
        for c in sup.clauses:
            if partial and tuple(c.path) in main_paths:
                continue
            key = (c.path, _norm(c.text))
            if key in seen:
                continue
            seen.add(key)
            c.carrier = sup.label
            c.block_index += off
            c.start += sbase
            c.end += sbase
            clauses.append(c)
        base += sum(len(b.text) for b in sup.blocks) + 1

    # sec 必须在**合并之后**重算：每个载体各自切分时都从 0 开始，
    # 直接拼接会出现两段 "附件/0"（2026-11 实测 122 条重复）。
    seen_sec: dict[tuple[int, str], int] = {}
    for c in clauses:
        key = (c.part, c.path)
        c.sec = seen_sec.get(key, 0)
        seen_sec[key] = c.sec + 1

    text = "\n".join(b.text for b in blocks)
    # 偏移必须以**合并后的全文**为准：主载体按块数累加、补充分支再加 base 都会差几行，
    # 结果 start 不再单调（2026-11 实测 18 份文档），父子区间检查随之失真。
    relocate(blocks, clauses)
    # 补充载体的文字与主副本的块布局未必一致，relocate 按块锚点算出的区间
    # 可能落在别的段落上（2026-11 体检 I0 抓到 59 条）。这里用文本回找兜底：
    # 找得到就按真实位置改正，找不到保持原样（由体检 I0 继续报）。
    for c in clauses:
        if text[c.start:c.end] != c.text:
            pos = text.find(c.text)
            if pos >= 0:
                c.start, c.end = pos, pos + len(c.text)
    # 还剩一种情况：补充载体的文字在主副本里**根本不存在**（去重/清洗后块内容变了）。
    # 直接丢掉会丢内容，所以把这条文本作为新块追加到末尾，坐标随之确定，
    # 保证"条款文本 == 全文[start:end]"这条不变量恒成立（体检 I0）。
    orphans = [c for c in clauses if text[c.start:c.end] != c.text]
    for c in orphans:
        start = len(text) + (1 if text else 0)
        blocks.append(Block(len(blocks), "para", c.text, c.page))
        text = (text + "\n" + c.text) if text else c.text
        c.block_index = blocks[-1].index
        c.start, c.end = start, start + len(c.text)
    # 表格结构随主副本走，补充载体的表追加在后面
    tables = list(p.main.tables)
    for sup, _partial in p.supplements:
        tables.extend(sup.tables)
    clauses.sort(key=lambda c: (c.start, c.end))
    out = dict(rec)
    out.update({
        "parser_version": PARSER_VERSION,
        "page_kind": p.main.kind,
        "page_sha256": p.main.sha256,
        "extract": p.main.extract,
        "ocr_conf": p.main.ocr_conf,
        "stats": {
            "blocks": len(blocks), "chars": len(text), "clauses": len(clauses),
            "tables": len(tables),
            "by_level": {str(k): sum(1 for c in clauses if c.level == k)
                         for k in sorted({c.level for c in clauses})},
        },
        "clauses": [
            {"path": c.path, "label": c.label, "level": c.level, "text": c.text,
             "page": c.page, "block_index": c.block_index, "char_offset": c.char_offset,
             "start": c.start, "end": c.end, "sec": c.sec, "part": c.part,
             "carrier": c.carrier}
            for c in clauses
        ],
        "tables": [t.as_dict(i) for i, t in enumerate(tables)],
        "blocks": [{"i": b.index, "kind": b.kind, "page": b.page, "text": b.text}
                   for b in blocks],
        # 到期日只认正文（列表页的"有效期至"是当前效力，可能已被延期，混用会让
        # steps 的第一步等于延期后的终点）。旧记录里 hint 派生的值在这里被覆盖。
        **_dates(text, rec),
        "carriers": [c.summary() for c in cands],
        "carrier_choice": {
            "role": p.main.role, "idx": p.main.idx, "kind": p.main.kind,
            "extract": p.main.extract, "reason": p.reason,
            "supplements": [dict(c.summary(), partial=pt) for c, pt in p.supplements],
            "dropped": [dict(c.summary(), why=w) for c, w in p.dropped],
        },
    })
    return out


def _norm(s: str) -> str:
    import re
    return re.sub(r"\s+", "", s or "")
