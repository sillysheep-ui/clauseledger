"""表格：先从载体里取出二维网格，再渲染成"表头：值"的行文本。

为什么不满足于"一行一块就完了"：

- 行文本丢了列名。问"罚款多少"、"有效期到什么时候"，命中的是
  `罚款 | 单位存在以下情形…`，列名只在第一行出现过一次，检索匹配不到。
- 结构不存。以后要按单元格问答（"第三行第二列是什么"）只能重新解析一遍。

所以两件事一起做：
1. `tables[]` 存 rows + page + bbox + source（可回溯到原件）
2. 块文本渲染成 `表头：值 | 表头：值`——分隔符仍是 ` | `，
   延期链的 `row_pairs()` 不受影响（它按文号找后面的日期）。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

CN = re.compile(r"[\u4e00-\u9fff]")
SEP = " | "


@dataclass
class Table:
    rows: list[list[str]] = field(default_factory=list)
    page: int | None = None
    bbox: list[float] | None = None
    source: str = "html"          # html / docx / pdf

    @property
    def header(self) -> list[str] | None:
        return header_of(self.rows)

    def render(self) -> list[str]:
        return render_rows(self.rows)

    def as_dict(self, index: int) -> dict:
        return {"i": index, "rows": self.rows, "page": self.page,
                "bbox": self.bbox, "source": self.source,
                "n_rows": len(self.rows),
                "n_cols": max((len(r) for r in self.rows), default=0)}


def cell_text(s: str | None) -> str:
    s = (s or "").replace("\u00a0", " ").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", s).strip()


def grid_from_html(tbl) -> list[list[str]]:
    rows = []
    for tr in tbl.find_all("tr"):
        cells = [cell_text(td.get_text(" ", strip=True))
                 for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if sum(len(CN.findall(c)) for c in cells) >= 2:
            rows.append(cells)
    return rows


def grid_from_docx(tbl, document) -> list[list[str]]:
    from docx.table import Table as _T

    rows = []
    for row in _T(tbl, document).rows:
        cells = [cell_text(c.text) for c in row.cells]
        # 合并单元格在 python-docx 里会重复返回同一个 cell，去掉相邻重复
        cells = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
        if sum(len(CN.findall(c)) for c in cells) >= 2:
            rows.append(cells)
    return rows


def grids_from_pdf(data: bytes) -> list[tuple[int, list[list[str]], list[float] | None]]:
    """文本层 PDF 才有；扫描件 `page.chars` 为空，直接跳过（交给 OCR 线文本）。"""
    try:
        import pdfplumber
    except Exception:
        return []
    out: list[tuple[int, list[list[str]], list[float] | None]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pno, page in enumerate(pdf.pages, 1):
                if not page.chars:
                    continue
                for t in page.find_tables():
                    rows = [[cell_text(c) for c in row if cell_text(c)]
                            for row in (t.extract() or [])]
                    rows = [r for r in rows if sum(len(CN.findall(x)) for x in r) >= 2]
                    if not plausible(rows):
                        continue   # 单栏版式被误判成表格，见 plausible()
                    out.append((pno, rows, list(t.bbox) if t.bbox else None))
    except Exception:
        return out
    return out


def covered_by(text: str, blob: str, thr: float = 0.6) -> bool:
    """这段文字是不是表格内容的另一种排版（PDF 的行文本与表格单元格重复）。"""
    if len(text) < 12 or not blob:
        return False
    t = re.sub(r"\s+", "", text)
    b = re.sub(r"\s+", "", blob)
    if len(t) < 12:
        return False
    grams = {t[i:i + 4] for i in range(len(t) - 3)}
    if not grams:
        return False
    hit = sum(1 for g in grams if g in b)
    return hit / len(grams) >= thr


def plausible(rows: list[list[str]]) -> bool:
    """pdfplumber 会把"一列文字"识别成单列表格（页面边框/对齐线造成）。

    判据：至少两行、多数行有 ≥2 个单元格、平均单元格长度 < 80 字。
    HTML/DOCX 的 `<table>` 不需要这条——标签本身就说明它是表。
    """
    if len(rows) < 2:
        return False
    widths = [len(r) for r in rows]
    if max(widths) < 2:
        return False
    if sum(1 for w in widths if w >= 2) < 0.6 * len(widths):
        return False
    cells = [c for r in rows for c in r]
    return bool(cells) and sum(len(c) for c in cells) / len(cells) < 80


def header_of(rows: list[list[str]]) -> list[str] | None:
    """第一行是不是表头：没有数字、列名互不相同、至少两列。"""
    if len(rows) < 2:
        return None
    first = [c for c in rows[0] if c]
    if len(first) < 2 or len(first) > 8:
        return None
    if any(re.search(r"\d", c) for c in first):
        return None
    if len(set(first)) != len(first):
        return None
    return first


def render_rows(rows: list[list[str]]) -> list[str]:
    """表头行原样渲染；数据行把列名拼进单元格。"""
    if not rows:
        return []
    head = header_of(rows)
    out: list[str] = []
    for i, row in enumerate(rows):
        cells = [c for c in row if c]
        if sum(len(CN.findall(c)) for c in cells) < 2:
            continue
        if head and i > 0:
            merged = []
            for j, c in enumerate(cells):
                if j < len(head) and head[j] and c and c != head[j]:
                    merged.append(f"{head[j]}：{c}")
                else:
                    merged.append(c)
            cells = merged
        out.append(SEP.join(cells))
    return out
