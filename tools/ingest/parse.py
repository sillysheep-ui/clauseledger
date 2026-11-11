"""三种载体转成同一种东西：有序的块（带页码）。"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

CN = re.compile(r"[\u4e00-\u9fff]")
SCAN_PAGE_CHARS = 50

# 解析器版本：偏移量只在同一版本内有效，换版本必须重算引用
PARSER_VERSION = "0.3.1"


@dataclass
class Block:
    index: int
    kind: str          # head / para / table
    text: str
    page: int | None = None


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    title: str = ""
    attachments: list[str] = field(default_factory=list)
    extract: str = "native"   # native / ocr / textutil / unsupported
    ocr_conf: float | None = None

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


def _clean(s: str) -> str:
    return re.sub(r"[\u00a0\u3000]+", " ", s).strip()


def parse_html(data: bytes, base_url: str) -> Parsed:
    soup = BeautifulSoup(data, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()
    title = ""
    if soup.h1 and soup.h1.get_text(strip=True):
        title = _clean(soup.h1.get_text())
    elif soup.title:
        title = _clean(soup.title.get_text())

    # 正文容器：先认站点通用正文 id/class，但**必须跟"最大文字块"比大小**——
    # 选择器命中一个小 div（.content 常常只是侧栏）会把正文截断，这比没命中更危险。
    cands = [c for c in soup.find_all(["article", "div", "section"])
             if len(CN.findall(c.get_text())) > 80]
    biggest = max(cands, key=lambda c: len(CN.findall(c.get_text()))) if cands else None
    root = None
    for sel in ("#ivs_content", ".Article_content", "#zoom", ".article-content",
                "article", ".article", "#content", ".content", ".TRS_Editor"):
        el = soup.select_one(sel)
        if el is not None and len(CN.findall(el.get_text())) > 80:
            root = el
            break
    if root is None:
        root = biggest or soup.body or soup
    elif biggest is not None and len(CN.findall(root.get_text())) < 0.7 * len(CN.findall(biggest.get_text())):
        root = biggest   # 选择器只圈到了一小块，退回最大文字块

    blocks: list[Block] = []

    def own_text(el) -> str:
        """只取该元素自己的文字，不含子块级元素的文字——避免父子重复计数。"""
        return " ".join(str(x) for x in el.find_all(string=True, recursive=False)).strip()

    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "table", "li", "div"], recursive=True):
        if el.name == "li" and el.find_parent("table"):
            continue
        if el.name == "div":
            # 页面把正文直接写在 div 里（上海人社就是这样），但 div 套 div 会重复
            has_block_child = el.find(["p", "div", "table", "li"], recursive=False) is not None
            txt = _clean(own_text(el) if has_block_child else el.get_text(" ", strip=True))
        else:
            txt = _clean(el.get_text(" ", strip=True))
        if len(CN.findall(txt)) < 2:
            continue
        kind = "head" if el.name in ("h1", "h2", "h3", "h4") else ("table" if el.name == "table" else "para")
        blocks.append(Block(len(blocks), kind, txt))

    atts = []
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        if re.search(r"\.(pdf|docx?|wps)(\?|$)", h):
            atts.append(httpx_urljoin(base_url, a["href"]))
    return Parsed(blocks, title, list(dict.fromkeys(atts)))


def httpx_urljoin(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)


def parse_pdf(data: bytes, path: str | None = None) -> Parsed:
    from pypdf import PdfReader

    title = ""
    total = 0
    pages: list[tuple[int, str]] = []
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:  # 政务 PDF 常见「打开密码为空」的权限加密
            reader.decrypt("")
        for i, page in enumerate(reader.pages):
            pages.append((i + 1, page.extract_text() or ""))
        total = sum(len(CN.findall(t)) for _, t in pages)
        title = _clean((reader.metadata or {}).get("/Title") or "")
    except Exception:
        # 解析器自己打不开（加密/损坏），交给 OCR 渲染，而不是抛给上层
        pages, total = [], 0

    # 中文总数不到「每页 50 字」，判为扫描件，走 OCR 兜底
    if total < SCAN_PAGE_CHARS * max(1, len(pages) or 1) and path:
        from tools.ingest.ocr import ocr_pdf
        try:
            texts, conf = ocr_pdf(path)
        except Exception:
            return Parsed(extract="unsupported")
        if sum(len(CN.findall(t)) for t in texts) > total:
            blocks: list[Block] = []
            for pg, t in enumerate(texts, 1):
                for raw in t.splitlines():
                    ln = _clean(raw)
                    if len(CN.findall(ln)) >= 2:
                        blocks.append(Block(len(blocks), "para", ln, page=pg))
            return Parsed(blocks, title, extract="ocr", ocr_conf=round(conf, 3))

    blocks: list[Block] = []
    for pg, t in pages:
        for raw in t.splitlines():
            ln = _clean(raw)
            if len(CN.findall(ln)) >= 2:
                blocks.append(Block(len(blocks), "para", ln, page=pg))
    return Parsed(blocks, title)


def parse_docx(data: bytes) -> Parsed:
    import docx

    d = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    for p in d.paragraphs:
        txt = _clean(p.text)
        if len(CN.findall(txt)) < 2:
            continue
        style = (p.style.name or "").lower()
        blocks.append(Block(len(blocks), "head" if "heading" in style else "para", txt))
    return Parsed(blocks)


def parse_doc(path: str) -> Parsed:
    """.doc（OLE2）→ textutil。拿不到就返回 unsupported，不许假装读到了。"""
    from tools.ingest.ocr import doc_to_text
    txt = doc_to_text(path)
    if not txt:
        return Parsed(extract="unsupported")
    blocks: list[Block] = []
    for raw in txt.splitlines():
        ln = _clean(raw)
        if len(CN.findall(ln)) >= 2:
            blocks.append(Block(len(blocks), "para", ln))
    return Parsed(blocks, extract="textutil")


def parse_any(data: bytes, content_type: str, url: str = "",
              path: str | None = None) -> tuple[str, Parsed]:
    ct = (content_type or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        return "pdf", parse_pdf(data, path)
    if url.lower().endswith(".doc") and not url.lower().endswith(".docx"):
        p = parse_doc(path) if path else Parsed(extract="unsupported")
        return ("doc" if p.blocks else "doc_unsupported"), p
    if "word" in ct or "officedocument" in ct or url.lower().endswith(".docx"):
        try:
            return "docx", parse_docx(data)
        except Exception:
            return "doc_unsupported", Parsed(extract="unsupported")
    return "html", parse_html(data, url)
