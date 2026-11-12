"""一份文件走完整条链：页面 → 附件 → 条款 → 结构化记录。

顺序很重要：先解析页面（拿标题与附件链接），再解析附件（正文通常在那），
最后一起切条款——因为条款编号可能跨页/跨载体连续。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tools.ingest.clause import split_clauses
from tools.ingest.fetch import fetch, sha
from tools.ingest.parse import PARSER_VERSION, Block, parse_any

CST = timezone(timedelta(hours=8))
OUT = Path("data/processed")
MAX_ATT = 3

RE_DOCNO = re.compile(r"[\u4e00-\u9fff]{2,10}\s*[〔\[［]\s*20\d{2}\s*[〕\]］]\s*\d+\s*号")
RE_EFF_FROM = re.compile(r"自\s*(20\d{2}年\d{1,2}月\d{1,2}日)\s*起\s*(?:施行|实施|执行|生效)")
RE_EFF_TO = re.compile(r"有效期(?:至|到)\s*(20\d{2}年\d{1,2}月\d{1,2}日)")


def iso(d: str | None) -> str | None:
    """'2027年1月1日' -> '2027-01-01'；已经是 ISO 的原样返回。"""
    if not d:
        return None
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", d.strip())
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else d


def drop_nav(blocks: list[Block]) -> list[Block]:
    """页面里的服务清单/面包屑会重复出现，重复 >=3 次的一律视为导航而不是正文。"""
    from collections import Counter
    cnt = Counter(b.text for b in blocks)
    kept = [b for b in blocks if cnt[b.text] < 3]
    return [Block(i, b.kind, b.text, b.page) for i, b in enumerate(kept)]


def ingest(url: str, *, slug: str | None = None, hint: dict | None = None) -> dict:
    """hint = 列表页里的元数据（doc_no / title / issued_at / valid_until）。

    文号优先用列表页的值：正文里的文号多是**引用别人**（国发〔2021〕7号），
    从正文猜必然猜错，这不是正则不够好，是位置不对。
    """
    page = fetch(url)
    kind, parsed = parse_any(page.content, page.content_type, url, page.path)
    blocks: list[Block] = [Block(i, b.kind, b.text, b.page) for i, b in enumerate(parsed.blocks)]
    atts = []
    for a in parsed.attachments[:MAX_ATT]:
        try:
            f = fetch(a, referer=url)
            akind, aparsed = parse_any(f.content, f.content_type, a, f.path)
            for b in aparsed.blocks:
                blocks.append(Block(len(blocks), b.kind, b.text, b.page))
            atts.append({"url": a, "kind": akind, "sha256": f.sha256,
                         "chars": len(aparsed.text), "blocks": len(aparsed.blocks),
                         "extract": aparsed.extract, "ocr_conf": aparsed.ocr_conf})
        except Exception as e:  # 附件失败不能拖垮整份文件，但要记下来
            atts.append({"url": a, "error": type(e).__name__})

    blocks = drop_nav(blocks)
    text = "\n".join(b.text for b in blocks)
    m_doc = RE_DOCNO.search(text) or RE_DOCNO.search(parsed.title)
    m_from = RE_EFF_FROM.search(text)
    m_to = RE_EFF_TO.search(text)
    clauses = split_clauses(blocks)

    hint = hint or {}
    doc_no = hint.get("doc_no") or (m_doc.group(0).replace(" ", "") if m_doc else None)
    rec = {
        "schema": "doc/v1",
        "doc_no": doc_no,
        "doc_no_source": "list" if hint.get("doc_no") else ("text" if m_doc else None),
        "meta_hint": hint or None,
        "title": hint.get("title") or parsed.title,
        # 列表页的元数据必须落到顶层：之前只留在 meta_hint 里，
        # 结果 560 份文档的 issued_at 全是空的，等于没有发文日期。
        "issued_at": iso(hint.get("issued_at")),
        "valid_until": iso(hint.get("valid_until")),
        "url": url,
        "fetched_at": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "page_kind": kind,
        "page_sha256": page.sha256,
        "parser_version": PARSER_VERSION,
        "attachments": atts,
        "retrieved_via": "crawl",
        "extract": parsed.extract,
        "ocr_conf": parsed.ocr_conf,
        "effective_from": iso(m_from.group(1) if m_from else None),
        "effective_to": iso(hint.get("valid_until") or (m_to.group(1) if m_to else None)),
        "stats": {"blocks": len(blocks), "chars": len(text), "clauses": len(clauses),
                  "by_level": {str(k): sum(1 for c in clauses if c.level == k)
                               for k in sorted({c.level for c in clauses})}},
        "clauses": [
            {"path": c.path, "label": c.label, "level": c.level, "text": c.text,
             "page": c.page, "block_index": c.block_index, "char_offset": c.char_offset,
             "start": c.start, "end": c.end}
            for c in clauses
        ],
        "blocks": [{"i": b.index, "kind": b.kind, "page": b.page, "text": b.text} for b in blocks],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    name = slug or re.sub(r"[^\w\u4e00-\u9fff]+", "-", (rec["doc_no"] or parsed.title[:24]))[:60]
    (OUT / f"{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    return rec
