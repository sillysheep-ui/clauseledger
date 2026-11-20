#!/usr/bin/env python3
"""M0 探测器：只测量，不做城市特化解析。

它回答四个问题，每个都是后面决策的输入：
  1. 这一页能不能拿到（源可达）
  2. 规则在不在这一页（正文条款数 / 是否提到关键阈值）
  3. 附件能不能发现、能不能下（附件发现率 / 可下载率）
  4. 条款能不能切、能不能结构化（条款边界 / 可结构化比例）

刻意不做的事：
  - 不按城市写选择器（M0 是测量，不是接入）
  - 不解析语义，只数结构
  - 不并发，1 req/s，带 UA 和退避
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

UA = "ClauseLedger-M0/0.1 (research probe; contact: liuduo_sky@163.com)"
TIMEOUT = 25.0
DELAY = 1.0

# 四套编号体系。不同城市/文种用哪套完全不同，不能只认"第X条"——
# 上海 2026 落户附件里"第X条"出现 0 次，"一、"29 次、"1."69 次。
NUMBERING = {
    "article": re.compile(r"第[一二三四五六七八九十百零〇\d]{1,6}条"),
    "han": re.compile(r"^\s*[一二三四五六七八九十]{1,3}、", re.M),
    "paren": re.compile(r"（[一二三四五六七八九十]{1,3}）"),
    "arabic": re.compile(r"^\s*\d{1,2}[.、]\s*\S", re.M),
}
DOC_NO_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d{1,4}\s*号")
DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
# 结构化线索：一条条款里出现这些词，说明它可能带适用条件
COND_HINTS = (
    "学历", "本科", "硕士", "博士", "毕业", "社保", "缴纳", "户籍", "户口",
    "年龄", "周岁", "居住证", "劳动合同", "积分", "分数", "自202", "自 202",
)


@dataclass
class Page:
    url: str
    status: int = 0
    final_url: str = ""
    content_type: str = ""
    bytes: int = 0
    elapsed_ms: int = 0
    kind: str = ""              # html / pdf / other
    text_chars: int = 0
    tables: int = 0
    clauses: int = 0
    cond_clauses: int = 0       # 命中 COND_HINTS 的条款数
    numbering: str = ""         # 主编号体系
    numbering_counts: dict = field(default_factory=dict)
    doc_no: str = ""
    dates: int = 0
    attachments: list = field(default_factory=list)
    error: str = ""

    @property
    def reachable(self) -> bool:
        return self.status == 200 and (self.text_chars >= 800 or self.kind == "pdf")


def fetch(url: str) -> tuple[Page, bytes]:
    page = Page(url=url)
    t0 = time.monotonic()
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        ) as client:
            r = client.get(url)
        page.status = r.status_code
        page.final_url = str(r.url)
        page.content_type = r.headers.get("content-type", "").split(";")[0].strip()
        page.bytes = len(r.content)
        page.elapsed_ms = int((time.monotonic() - t0) * 1000)
        body = r.content
        if page.content_type == "application/pdf" or body[:5] == b"%PDF-":
            page.kind = "pdf"
        elif "html" in page.content_type:
            page.kind = "html"
        else:
            page.kind = "other"
        return page, body
    except Exception as e:  # noqa: BLE001 - 探测器要吞掉一切网络异常并记录
        page.error = f"{type(e).__name__}: {e}"
        page.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return page, b""


def pdf_text_chars(body: bytes, max_pages: int = 3) -> tuple[int, int]:
    """返回 (页数, 前 max_pages 页可提取字符数)。没有 pypdf 时页数用粗略统计。"""
    pages, chars = 0, 0
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        pages = len(reader.pages)
        for p in reader.pages[:max_pages]:
            chars += len((p.extract_text() or "").strip())
    except Exception:  # noqa: BLE001
        pages = body.count(b"/Type /Page") - body.count(b"/Type /Pages")
        chars = -1  # 未知
    return max(pages, 0), chars


def detect_numbering(text: str) -> tuple[str, dict[str, int]]:
    """返回（主体系, 各体系命中数）。主体系取命中最多者，全 <5 视为无结构。"""
    counts = {name: len(rx.findall(text)) for name, rx in NUMBERING.items()}
    # 按"粗到细"取第一个成立的体系：法条 > 一、 > （一） > 1.
    # 取最大值会选到明细里的阿拉伯序号，把"条"切碎成表格行。
    for name in ("article", "han", "paren", "arabic"):
        if counts[name] >= 5:
            return name, counts
    return "", counts


def split_clauses(text: str, system: str = "article") -> list[str]:
    rx = NUMBERING.get(system) or NUMBERING["article"]
    hits = [m.start() for m in rx.finditer(text)]
    if not hits:
        return []
    out = []
    for i, start in enumerate(hits):
        end = hits[i + 1] if i + 1 < len(hits) else min(len(text), start + 1200)
        seg = text[start:end].strip()
        if len(seg) >= 10:
            out.append(seg)
    return out


def measure_html(page: Page, body: bytes) -> None:
    html = body.decode("utf-8", errors="ignore")
    if "charset=gb" in page.content_type.lower() or 'charset="gb' in html[:2000].lower():
        html = body.decode("gb18030", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n"))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    page.text_chars = len(text)
    page.tables = len(soup.find_all("table"))
    system, counts = detect_numbering(text)
    page.numbering = system
    page.numbering_counts = counts
    clauses = split_clauses(text, system) if system else []
    page.clauses = len(clauses)
    page.cond_clauses = sum(1 for c in clauses if any(h in c for h in COND_HINTS))
    m = DOC_NO_RE.search(text)
    page.doc_no = m.group(0) if m else ""
    page.dates = len(DATE_RE.findall(text))

    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        label = a.get_text(strip=True)
        lower = href.lower()
        is_file = any(lower.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx"))
        is_att = is_file or any(k in label for k in ("附件", "下载", "全文"))
        if not (is_file or is_att):
            continue
        absolute = urljoin(page.final_url or page.url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        page.attachments.append({"label": label[:40], "url": absolute})


def pdf_full_text(body: bytes, max_pages: int = 80) -> str:
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(body))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:max_pages])
    except Exception:  # noqa: BLE001
        return ""


def measure_pdf(page: Page, body: bytes) -> None:
    text = pdf_full_text(body)
    page.text_chars = len(text.strip())
    system, counts = detect_numbering(text)
    page.numbering = system
    page.numbering_counts = counts
    clauses = split_clauses(text, system) if system else []
    page.clauses = len(clauses)
    page.cond_clauses = sum(1 for c in clauses if any(h in c for h in COND_HINTS))
    m = DOC_NO_RE.search(text)
    page.doc_no = m.group(0) if m else ""
    page.dates = len(DATE_RE.findall(text))


def probe(url: str, follow_attachments: int = 3) -> dict:
    page, body = fetch(url)
    result = asdict(page)

    if page.kind == "html" and body:
        measure_html(page, body)
    elif page.kind == "pdf" and body:
        pages, chars = pdf_text_chars(body)
        measure_pdf(page, body)
        result["pdf_pages"] = pages
        result["pdf_text_chars_first3"] = chars
        result["scanned_likely"] = chars == 0

    result = asdict(page)
    result["reachable"] = page.reachable
    if "pdf_pages" not in result:
        result["pdf_pages"] = 0

    # 附件只测前 N 个，记录能不能下、是不是扫描件
    att_results = []
    for att in page.attachments[:follow_attachments]:
        time.sleep(DELAY)
        ap, abody = fetch(att["url"])
        item = {
            "url": att["url"],
            "label": att["label"],
            "status": ap.status,
            "content_type": ap.content_type,
            "bytes": ap.bytes,
            "ok": ap.status == 200 and ap.bytes > 2000,
        }
        if ap.kind == "pdf" and abody:
            pages, chars = pdf_text_chars(abody)
            measure_pdf(ap, abody)
            item["pdf_pages"] = pages
            item["text_chars_first3"] = chars
            item["scanned_likely"] = chars == 0
            item["numbering"] = ap.numbering
            item["numbering_counts"] = ap.numbering_counts
            item["clauses"] = ap.clauses
            item["cond_clauses"] = ap.cond_clauses
        att_results.append(item)

    result["attachment_probe"] = att_results
    return result


def score(rows: list[dict]) -> dict:
    """四道门，全是布尔与计数，不做加权。"""
    n = len(rows)
    if n == 0:
        return {}
    reach = sum(1 for r in rows if r["reachable"])
    with_clause = sum(1 for r in rows if r["clauses"] >= 5)
    cond = sum(r["cond_clauses"] for r in rows)
    clauses = sum(r["clauses"] for r in rows)
    att_total = sum(len(r["attachments"]) for r in rows)
    att_ok = sum(1 for r in rows for a in r["attachment_probe"] if a["ok"])
    att_tried = sum(len(r["attachment_probe"]) for r in rows)
    scanned = sum(1 for r in rows for a in r["attachment_probe"] if a.get("scanned_likely"))
    return {
        "样本数": n,
        "可达率": round(reach / n, 3),
        "带附件页面数": sum(1 for r in rows if r["attachments"]),
        "附件链接数": att_total,
        "附件可下载率": round(att_ok / att_tried, 3) if att_tried else 0.0,
        "疑似扫描件": scanned,
        "条款总数": clauses,
        "可结构化条款": cond,
        "可结构化比例": round(cond / clauses, 3) if clauses else 0.0,
        "条款可切率": round(with_clause / n, 3),
        "G1_源可达": reach == n,
        "G2_附件可下载": (att_ok / att_tried >= 0.8) if att_tried else False,
        "G3_条款可切": with_clause / n >= 0.8,
        "G4_可结构化": (cond / clauses >= 0.6) if clauses else False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", help="JSON 文件，形如 {\"city\": ..., \"items\": [{\"topic\",\"url\"}]}")
    ap.add_argument("--out", default="data/fixtures/scan")
    args = ap.parse_args()

    seeds = json.loads(Path(args.seeds).read_text("utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows, summary = [], {}
    for city in seeds["cities"]:
        rows = []
        for item in city["items"]:
            print(f"[{city['city']}] {item['topic']} {item['url']}", file=sys.stderr)
            r = probe(item["url"])
            r["topic"] = item["topic"]
            rows.append(r)
            all_rows.append({"city": city["city"], **r})
            time.sleep(DELAY)
        summary[city["city"]] = score(rows)

    stamp = time.strftime("%Y%m%d-%H%M")
    (out_dir / f"m0-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": all_rows}, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
