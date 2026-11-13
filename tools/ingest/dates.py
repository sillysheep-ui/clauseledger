"""从公文正文里抽日期字段。

四个日期不是一回事：
  issued_at      成文日期 —— 落款处的日期，文件"写于"哪一天
  published_at   印发日期 —— 列表页的"生成日期"，办公室印出来的日期
  effective_from 施行日期 —— 正文"自 X 年 X 月 X 日起施行"
  effective_to   有效期至 —— 正文"有效期至 X" 或列表页那一列

抽不到就是 None + source=unknown，不许猜、不许默认"长期有效"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

RE_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_DATE_ONLY = re.compile(r"^\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*$")
RE_ORG = re.compile(r"(局|委|厅|办公室|委员会|人民政府|管理局|管理中心|中心|院|署|会)\s*$")
RE_FROM = re.compile(
    r"自\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*起\s*(?:施行|实施|执行|生效)"
)
RE_TO = re.compile(
    r"有效期(?:至|到|截止)?\s*[:：]?\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
RE_DOCNO_YEAR = re.compile(r"[〔（(]\s*(19\d{2}|20\d{2})\s*[〕）)]")
RE_LONG = re.compile(r"长期有效|有效期至长?期")
# 日期后面紧跟这些字，说明它是"印发/发布"日期，不是成文日期
RE_AFTER_PRINT = re.compile(r"^\s*(印发|发布|公告|公布)")
# 块里出现这些词，说明这个日期属于"有效期/施行日"，不是落款
RE_NOT_SIGN = re.compile(r"(有效期|施行|实施|执行|生效|届满|截止)")
# 日期前面紧挨着"至/到"，是区间终点
RE_BEFORE_TO = re.compile(r"(至|到)\s*$")


def iso(y: str, m: str, d: str) -> str | None:
    """年月日转 ISO；非法日期返回 None（2 月 30 日这种不纠正）。"""
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def _iso_from_match(m: re.Match[str]) -> str | None:
    return iso(m.group(1), m.group(2), m.group(3))


def _iso_from_text(s: str) -> str | None:
    """把"2021年5月1日"这种自由文本转 ISO（RE_FROM/RE_TO 只捕获整段）。"""
    m = RE_DATE.search(s)
    return _iso_from_match(m) if m else None


def _is_print_date(text: str, m: re.Match[str]) -> bool:
    """这个日期是不是"印发/发布"日期。"""
    return bool(RE_AFTER_PRINT.match(text[m.end() : m.end() + 8]))


@dataclass
class Signs:
    """一次落款抽取的结果。"""

    date: str | None = None
    source: str = "unknown"  # sign_block | sign_inline | tail_date | unknown
    raw: str = ""  # 命中的原文，供人工核对
    block_index: int | None = None
    tried: int = 0  # 扫了多少块


def sign_date(blocks: list[dict], window: int = 24) -> Signs:
    """三级抽取，先严后松。

    P1 日期独立成块，且上一块以机关名结尾
    P2 同一块里既有机关名又有日期
    P3 末尾 12 块里最后一个"不是印发日期"的日期
    """
    if not blocks:
        return Signs()
    tail = blocks[-window:]
    base = len(blocks) - len(tail)
    tried = 0

    # P1：日期独立成块 + 上一块是机关名
    for i in range(len(tail) - 1, 0, -1):
        tried += 1
        b = tail[i]
        m = RE_DATE_ONLY.match(b["text"])
        if not m:
            continue
        if _is_print_date(b["text"], m):
            continue
        prev = tail[i - 1]["text"].strip()
        if RE_ORG.search(prev):
            return Signs(
                date=_iso_from_match(m),
                source="sign_block",
                raw=f"{prev} / {b['text'].strip()}",
                block_index=base + i,
                tried=tried,
            )

    # P2：机关名与日期同块
    for i in range(len(tail) - 1, -1, -1):
        b = tail[i]
        m = RE_DATE.search(b["text"])
        if not m or _is_print_date(b["text"], m):
            continue
        tried += 1
        prefix = b["text"][: m.start()].strip()
        if RE_ORG.search(prefix):
            return Signs(
                date=_iso_from_match(m),
                source="sign_inline",
                raw=b["text"].strip()[:80],
                block_index=base + i,
                tried=tried,
            )

    return Signs(source="unknown", tried=tried)


@dataclass
class Range:
    """施行日期与有效期。"""

    effective_from: str | None = None
    effective_to: str | None = None
    source: str = "unknown"  # text | long_term | list | unknown
    raw: str = ""


def effective_range(text: str) -> Range:
    """从全文抽"自…起施行"和"有效期至…"；两个都取第一次出现。"""
    frm = RE_FROM.search(text)
    eff_from = _iso_from_text(frm.group(1)) if frm else None

    to = RE_TO.search(text)
    if to:
        return Range(eff_from, _iso_from_text(to.group(1)), "text", to.group(0).strip()[:60])
    if RE_LONG.search(text):
        return Range(eff_from, None, "long_term", RE_LONG.search(text).group(0))  # type: ignore[union-attr]
    return Range(eff_from, None, "unknown", "")


def status_of(
    effective_from: str | None,
    effective_to: str | None,
    *,
    today: date | None = None,
) -> str:
    """四态：pending / effective / expired / unknown。到期日当天算有效。"""
    today = today or date.today()
    if effective_to:
        return "expired" if today.isoformat() > effective_to else "effective"
    if effective_from:
        return "pending" if today.isoformat() < effective_from else "effective"
    return "unknown"


@dataclass
class Dates:
    """一份文档的日期字段集合（写回记录的补丁）。"""

    issued_at: str | None = None
    issued_at_source: str = "unknown"
    published_at: str | None = None
    published_at_source: str = "unknown"
    effective_from: str | None = None
    effective_to: str | None = None
    effective_to_source: str = "unknown"
    date_note: str = ""
    issued_year: int | None = None
    date_precision: str = "unknown"  # day | year | unknown
    signs: Signs = field(default_factory=Signs)
    range: Range = field(default_factory=Range)

    def as_fields(self) -> dict[str, str | None]:
        return {
            "issued_at": self.issued_at,
            "issued_at_source": self.issued_at_source,
            "published_at": self.published_at,
            "published_at_source": self.published_at_source,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "effective_to_source": self.effective_to_source,
            "date_note": self.date_note,
            "issued_year": self.issued_year,
            "date_precision": self.date_precision,
        }


def extract(doc: dict) -> Dates:
    """从一条 processed 记录里抽出全部日期字段。

    优先级：
      issued_at    ← 落款（三级）；抽不到才退回列表日期，并标 list
      effective_to ← 列表页有效期列 > 正文 > long_term > unknown
    """
    blocks = doc.get("blocks") or []
    text = "\n".join(b.get("text", "") for b in blocks)

    signs = sign_date(blocks)
    rng = effective_range(text)

    hint = doc.get("meta_hint") or {}
    m = RE_DOCNO_YEAR.search(str(doc.get("doc_no") or ""))
    if m:
        out_year = int(m.group(1))
    list_date = doc.get("published_at") or doc.get("issued_at") or hint.get("issued_at")
    list_valid = doc.get("effective_to") or hint.get("valid_until")

    out = Dates(signs=signs, range=rng)
    out.issued_year = locals().get("out_year")
    out.published_at = list_date or None
    out.published_at_source = "list" if list_date else "unknown"

    if signs.date:
        out.issued_at, out.issued_at_source = signs.date, signs.source
        out.date_precision = "day"
    elif list_date:
        out.issued_at, out.issued_at_source = list_date, "list"
        out.date_precision = "day"
        out.date_note = "未找到落款，用列表日期"
    else:
        out.issued_at, out.issued_at_source = None, "unknown"
        out.date_precision = "year" if out.issued_year else "unknown"
        out.date_note = "未找到落款与列表日期，只有年份"

    out.effective_from = rng.effective_from
    if list_valid:
        out.effective_to, out.effective_to_source = list_valid, "list"
    elif rng.effective_to:
        out.effective_to, out.effective_to_source = rng.effective_to, "text"
    elif rng.source == "long_term":
        out.effective_to, out.effective_to_source = None, "long_term"
        out.date_note = (out.date_note + "；" if out.date_note else "") + "正文写明长期有效"
    else:
        out.effective_to, out.effective_to_source = None, "unknown"
        out.date_note = (out.date_note + "；" if out.date_note else "") + "未查到有效期"

    if signs.date and list_date and signs.date != list_date:
        out.date_note = (
            out.date_note + "；" if out.date_note else ""
        ) + f"落款 {signs.date} 与列表日期 {list_date} 不一致"

    return out
