"""三种载体转成同一种东西：有序的块（带页码）。"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

CN = re.compile(r"[\u4e00-\u9fff]")

# 解析器版本：偏移量只在同一版本内有效，换版本必须重算引用
PARSER_VERSION = "0.1.0"


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

    # 正文容器：<p> 里中文最多的那个（导航栏不会有长段落）
    best, best_score = None, 0
    for c in soup.find_all(["article", "div", "section"]):
        score = sum(len(CN.findall(p.get_text())) for p in c.find_all("p", recursive=True))
        if score > best_score:
            best, best_score = c, score
    root = best or soup.body or soup

    blocks: list[Block] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "table", "li"], recursive=True):
        if el.name == "li" and el.find_parent("table"):
            continue
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


def parse_pdf(data: bytes) -> Parsed:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    blocks: list[Block] = []
    for i, page in enumerate(reader.pages):
        txt = page.extract_text() or ""
        for line in txt.splitlines():
            line = _clean(line)
            if len(CN.findall(line)) >= 2:
                blocks.append(Block(len(blocks), "para", line, page=i + 1))
    title = (reader.metadata or {}).get("/Title") or ""
    return Parsed(blocks, _clean(title))


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


def parse_any(data: bytes, content_type: str, url: str = "") -> tuple[str, Parsed]:
    ct = (content_type or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        return "pdf", parse_pdf(data)
    if "word" in ct or "officedocument" in ct or url.lower().endswith((".docx", ".doc")):
        try:
            return "docx", parse_docx(data)
        except Exception:  # .doc 老格式 python-docx 打不开
            return "doc_unsupported", Parsed()
    return "html", parse_html(data, url)
