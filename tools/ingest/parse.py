"""三种载体转成同一种东西：有序的块（带页码）。"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment

from tools.ingest.tables import Table, covered_by, grid_from_docx, grid_from_html, grids_from_pdf, render_rows

CN = re.compile(r"[\u4e00-\u9fff]")
SCAN_PAGE_CHARS = 50
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "p", "table", "li", "div")
CRUMB = ("您现在的位置", "当前位置", "相关链接", "首页 >", "首页>")
# 页尾控件：从第一个命中的块起，后面全是站点模板，不是公文正文。
# 不切掉的话，「相关稿件」「《上一篇…》」会拼进最后一条条款的正文里
# （评测里表现为 8% 的回答带着这段尾巴，见 clean 指标）。
FOOTER = re.compile(r"(打印本页|分享到|相关稿件|相关链接|返回顶部|关闭窗口|字体[:：]|上一篇|下一篇|扫一扫)")

# 解析器版本：偏移量只在同一版本内有效，换版本必须重算引用
PARSER_VERSION = "0.3.22"


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
    meta: dict = field(default_factory=dict)   # 页面 <meta>（PubDate/ContentSource 等）
    tables: list = field(default_factory=list)  # tools.ingest.tables.Table

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


TAG = re.compile(r"</?[a-zA-Z][^<>]{0,200}>")


def _clean(s: str) -> str:
    """去不换行空格、去正文里残留的 HTML 标签。

    有些页面把模板源码当文本节点吐出来（`<div class="Xun-tong">`），
    lxml 反转义后就变成正文的一部分——不清掉，它会一路污染到检索和引用。
    """
    s = TAG.sub(" ", s or "")
    s = re.sub(r"[\u00a0\u3000]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _table_rows(tbl) -> list[str]:
    """表格按行切成 `a | b | c`。

    一行一块，而不是整表一块：整表成块会把几十行压成一条，条款切分看不见行，
    检索也只能匹配到最长的那一条。按行切开，既不重复单元格，又保住匹配粒度。
    """
    rows = []
    for tr in tbl.find_all("tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if sum(len(CN.findall(c)) for c in cells) >= 2:
            rows.append(" | ".join(cells))
    return rows


META_KEYS = ("PubDate", "ContentSource", "ArticleTitle", "Keywords", "Description")


def parse_html(data: bytes, base_url: str) -> Parsed:
    soup = BeautifulSoup(data, "lxml")
    meta: dict = {}
    for m in soup.find_all("meta"):
        name = (m.get("name") or m.get("property") or "").strip()
        if name in META_KEYS and (m.get("content") or "").strip():
            meta[name.lower()] = m["content"].strip()
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
    parsed_tables: list = []

    def _walk(el):
        """按文档顺序走，但**不钻进 <table>**——表格只取一次，不取它的单元格。"""
        for ch in el.children:
            name = getattr(ch, "name", None)
            if name is None:
                continue
            if name in ("h1", "h2", "h3", "h4", "p", "table", "li", "div"):
                yield ch
                if name != "table":
                    yield from _walk(ch)
            else:
                yield from _walk(ch)

    def own_text(el) -> str:
        """只取该元素自己的文字，不含子块级元素的文字——避免父子重复计数。

        `Comment` 必须排除：被注释掉的 `<script>` 在 BeautifulSoup 里是注释节点，
        照样会被 `find_all(string=True)` 捞出来，于是 JS 源码当成正文进了语料
        （93 份文档各带一段 `const specialChars = …`）。
        """
        return " ".join(str(x) for x in el.find_all(string=True, recursive=False)
                        if not isinstance(x, Comment)).strip()

    for el in _walk(root):
        if el.name == "table":
            # 表格只吃一次就停：以前递归再进单元格，同一批字会以
            # `序号`/`文件名称`/`文号` 各成一块的形式重复一遍。
            rows = grid_from_html(el)
            if rows:
                parsed_tables.append(Table(rows=rows, source="html"))
            for row in render_rows(rows):
                if not row.startswith(CRUMB):
                    blocks.append(Block(len(blocks), "table", row))
            continue
        elif el.name == "div":
            # 页面把正文直接写在 div 里（上海人社就是这样），但 div 套 div 会重复
            has_block_child = el.find(["p", "div", "table", "li"], recursive=False) is not None
            txt = _clean(own_text(el) if has_block_child else el.get_text(" ", strip=True))
            kind = "para"
        else:
            txt = _clean(el.get_text(" ", strip=True))
            kind = "head" if el.name in ("h1", "h2", "h3", "h4") else "para"
        if len(CN.findall(txt)) < 2:
            continue
        if txt.startswith(CRUMB) or re.search(r"(?:class|href|src)=|<[a-zA-Z/]", txt):
            continue   # 面包屑与模板源码残片：不是正文
        if FOOTER.search(txt):
            break      # 正文到此为止，后面是页尾模板
        blocks.append(Block(len(blocks), kind, txt))

    atts = []
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        if re.search(r"\.(pdf|docx?|wps)(\?|$)", h):
            atts.append(httpx_urljoin(base_url, a["href"]))
    return Parsed(blocks, title, list(dict.fromkeys(atts)), meta=meta,
                  tables=parsed_tables)


def httpx_urljoin(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, href)



# PDF 的换行是排版换行，不是段落边界。一行一块会把"固定\n收入"切成两个词，
# 检索时既切断了词，也让条款正文变成碎行。所以在页内先把折行接回去。
HARD_BREAK = re.compile(
    r"^(第\s*[一二三四五六七八九十百\d]{1,4}\s*[条章节]|[一二三四五六七八九十]{1,3}\s*、"
    r"|[（(][一二三四五六七八九十\d]{1,3}[）)]|\d{1,2}\s*[.．、]|附\s*[录表件]|抄送)"
)
SENT_END = ("。", "！", "？", "；", "：", "!", "?", ";", ":")


def _merge_wrapped(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        if not out:
            out.append(ln)
            continue
        prev = out[-1]
        if HARD_BREAK.match(ln) or prev.endswith(SENT_END) or len(prev) < 6:
            out.append(ln)
        else:
            out[-1] = prev + ln      # 中文折行直接拼，不加空格
    return out


# 纯页码行：「— 3 —」「- 12 -」「3 / 56」
PAGE_NO = re.compile(r"^\s*(?:[—\-–]\s*\d{1,4}\s*[—\-–]|\d{1,4}\s*/\s*\d{1,4})\s*$")
# 注意：不能把"单独一行数字"当页码丢掉——PDF 里「〔2021〕」常被拆成「20」「21」两行，
# 一丢就整份文件没有年份（实测踩过，两份评测金标因此不可检索）。
# 句末标点：这些结尾的行是"完整的一句"，不该把下一行粘进来
TERM = re.compile(r"[。；！？：:」』)）]$")


def _squeeze_cjk(line: str) -> str:
    """把「上 海 市」这种为排版加的字间空格去掉。

    红头文件常用散排标题，PDF 抽出来就是每个字之间一个空格；不合并的话，
    分词会把它们切成单字，而单字在索引里会被丢掉（长度 <2），整段标题消失。
    """
    prev = None
    while prev != line:
        prev = line
        line = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", line)
    return line


def _merge_fragments(lines: list[str]) -> list[str]:
    """把被 PDF 拆碎的片段拼回去。

    实测：`沪人社规〔2021〕15号` 在文本层里是八行——「沪人社」「规」「〔」
    「20」「21」「〕」「15」「号」。旧规则"汉字少于 2 个的行丢掉"会把这些行
    全部扔掉，于是文号变成「〔 〕号」、正文里的「2021 年」变成「年」，
    整份文件的**数字全部消失**（全库 5 份 PDF 就是这样，其中两份是评测金标）。
    """
    out: list[str] = []
    for raw in lines:
        ln = _squeeze_cjk(_clean(raw))
        if not ln.strip() or PAGE_NO.match(ln):
            continue
        # 碎片：汉字少于 2 个、且不是完整句子 → 粘到上一行，不加分隔
        ln = re.sub(r"^[—\-–]\s*\d{1,4}\s*[—\-–]", "", ln).strip()
        if not ln:
            continue
        if out and len(CN.findall(ln)) < 2 and not TERM.search(ln):
            out[-1] = out[-1] + ln
        else:
            out.append(ln)
    return out


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
            from tools.ingest import vision
            scanned_tables = []
            if vision.looks_tabular([ln for t in texts for ln in t.splitlines()]):
                try:
                    scanned_tables = vision.tables(path, max_pages=min(len(texts), 12))
                except Exception:
                    scanned_tables = []   # 认不出表格不影响正文
            by_page: dict[int, list] = {}
            for tb in scanned_tables:
                by_page.setdefault(tb.page or 1, []).append(tb)
            blocks: list[Block] = []
            for pg, t in enumerate(texts, 1):
                page_tables = by_page.get(pg, [])
                blob = " ".join(c for tb in page_tables for r in tb.rows for c in r)
                for ln in _merge_wrapped(_merge_fragments(t.splitlines())):
                    # 同一批字被 OCR 一遍、又被表格还原一遍，留着会进库两次
                    if blob and covered_by(ln, blob):
                        continue
                    blocks.append(Block(len(blocks), "para", ln, page=pg))
                for tb in page_tables:
                    for text in tb.render():
                        blocks.append(Block(len(blocks), "table", text, page=pg))
            return Parsed(blocks, title, tables=scanned_tables,
                          extract="ocr", ocr_conf=round(conf, 3))

    grids: dict[int, list] = {}
    for pno, rows, bbox in grids_from_pdf(data):
        grids.setdefault(pno, []).append((rows, bbox))
    blocks: list[Block] = []
    tables: list = []
    for pg, t in pages:
        lines = list(_merge_wrapped(_merge_fragments(t.splitlines())))
        page_tables = grids.get(pg, [])
        blob = " ".join(c for rows, _ in page_tables for r in rows for c in r)
        for ln in lines:
            # PDF 的行文本与表格单元格是同一批字：表格页里那几行“序号文件名称文号…”
            # 如果原样留着，同一段内容会进库两遍（一遍无分隔、一遍带表头）。
            if blob and covered_by(ln, blob):
                continue
            blocks.append(Block(len(blocks), "para", ln, page=pg))
        for rows, bbox in page_tables:
            tables.append(Table(rows=rows, page=pg, bbox=bbox, source="pdf"))
            for row in render_rows(rows):
                blocks.append(Block(len(blocks), "table", row, page=pg))
    return Parsed(blocks, title, tables=tables)


def parse_docx(data: bytes) -> Parsed:
    import docx
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    tables: list = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            txt = _clean(p.text)
            if len(CN.findall(txt)) < 2:
                continue
            style = (p.style.name or "").lower()
            blocks.append(Block(len(blocks), "head" if "heading" in style else "para", txt))
        elif tag == "tbl":
            # Word 表格里的字在 `paragraphs` 里一个字都取不到——
            # 沪人社规〔2022〕20号 的三个 docx 附件就是因此抽成 0 字。
            rows = grid_from_docx(child, d)
            if rows:
                tables.append(Table(rows=rows, source="docx"))
            for row in render_rows(rows):
                blocks.append(Block(len(blocks), "table", row))
    return Parsed(blocks, tables=tables)


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
