"""用 macOS Vision 的**字级坐标**重建扫描件里的表格行。

为什么需要它：扫描件没有文本层（`pypdfium2` 的 `count_chars()` 为 0），
OCR 只能给出一串按阅读顺序拼起来的文字。列优先排版的表格（先一整列标题、
再一整列文号、再一整列日期）在这种情况下没法按"文号后面跟日期"配对。

`ocrmac` 不暴露逐块坐标（实测 bbox 全是 `[0,1,1,0]`），所以这里直接调 Vision：
每个观察结果带 `boundingBox`（归一化、原点在左下），有了 y 就知道哪些单元格
在同一行，于是「文号 → 日期」可以按**同一行**配对。

约束：只在同一页内配对；一对一，距离超过 1.5 倍中位行高就放弃。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

try:  # pragma: no cover - 只在 macOS 上可用
    import Quartz
    import Vision
    from Foundation import NSURL

    HAVE_VISION = True
except Exception:  # pragma: no cover
    HAVE_VISION = False

import re
import statistics

from tools.ingest.tables import Table, header_of

RE_DOCNO = re.compile(
    r"沪\s*[\u4e00-\u9fa5]{1,8}\s*[〔（(]\s*\d{2,4}\s*[〕）)]\s*第?\s*\d+\s*号"
)
RE_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_SEQ = re.compile(r"^\d{1,3}$")
# 页码必须带破折号/长横：只用 ^\d+$ 会把表格的序号列整列当页码删掉（2026-11 实测）。
RE_PAGE_HARD = re.compile(r"^\s*[—\-一]\s*\d+\s*[—\-一]\s*$")
RE_PAGE = re.compile(r"^[—\-一\s]*\d+[—\-一\s]*$")
# 单元格级的文号：比 RE_DOCNO 宽——像「沪劳(86)资创字第225号」这种带「字第」的
# 老文号，RE_DOCNO 匹配不上，但它在表格里就是一格。
RE_CELL_NO = re.compile(r"^沪.{3,26}号$")
HEADER_WORDS = {"序号", "文件名称", "文号", "延长有效期"}


def observations(pdf_path: str | Path, max_pages: int = 6) -> list[dict]:
    """逐页 OCR，返回带归一化坐标的观察结果（y 已翻成从上到下）。"""
    if not HAVE_VISION:
        return []
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    out: list[dict] = []
    for i in range(min(len(pdf), max_pages)):
        img = pdf[i].render(scale=2).to_pil()
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        try:
            url = NSURL.fileURLWithPath_(tmp.name)
            src = Quartz.CGImageSourceCreateWithURL(url, None)
            cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLanguages_(["zh-Hans"])
            req.setRecognitionLevel_(0)  # accurate
            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
            handler.performRequests_error_([req], None)
            for o in req.results() or []:
                bb = o.boundingBox()
                text = o.topCandidates_(1)[0].string()
                out.append({"page": i, "text": text,
                            "x": float(bb.origin.x), "y": 1 - float(bb.origin.y + bb.size.height),
                            "h": float(bb.size.height)})
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return out


HEADERS = {"序号": 0, "文件名称": 1, "文号": 2, "延长有效期": 3}


def _columns(obs: list[dict]) -> list[float] | None:
    """用表头的位置定列起点；找不到表头就返回 None。"""
    xs = {}
    for o in obs:
        t = o["text"].strip()
        for h, i in HEADERS.items():
            if t == h:
                xs[i] = o["x"]
    if len(xs) < 4:
        return None
    return [xs[i] for i in range(4)]


def _bounds(starts: list[float]) -> list[float]:
    out = [0.0]
    for a, b in zip(starts, starts[1:]):
        out.append((a + b) / 2)
    out.append(1.0)
    return out


def _cells(items: list[dict], complete, gap: float) -> list[tuple[float, str]]:
    """把同一列里被折行拆开的碎片拼回单元格，返回 (y, text)。"""
    items = sorted(items, key=lambda o: o["y"])
    out: list[tuple[float, str]] = []
    cur: list[dict] = []
    for o in items:
        if cur and o["y"] - cur[-1]["y"] > gap * 2.0:
            out.append((cur[0]["y"], "".join(x["text"] for x in cur)))
            cur = []
        cur.append(o)
        text = "".join(x["text"] for x in cur)
        if complete(text):
            out.append((cur[0]["y"], text))
            cur = []
    if cur:
        out.append((cur[0]["y"], "".join(x["text"] for x in cur)))
    return out


def row_pairs(pdf_path: str | Path) -> list[tuple[str, str]]:
    """在同一页里按行把「文号」和「日期」配起来。"""
    obs = observations(pdf_path)
    if not obs:
        return []
    heights = sorted(o["h"] for o in obs) or [0.02]
    gap = heights[len(heights) // 2]
    starts = _columns(obs)
    if starts is None:
        return []
    bounds = _bounds(starts)
    out: list[tuple[str, str]] = []
    for page in sorted({o["page"] for o in obs}):
        cells = [o for o in obs
             if o["page"] == page and o["text"].strip() not in HEADER_WORDS]
        nos = _cells([o for o in cells if bounds[2] <= o["x"] < bounds[3]],
                     lambda t: bool(RE_CELL_NO.match(t.strip())), gap)
        dates = _cells([o for o in cells if o["x"] >= bounds[3]],
                       lambda t: bool(RE_DATE.search(t)), gap)
        # 同一页内第 i 个文号配第 i 个日期；数量不等就整页放弃
        nos = [(y, t.strip()) for y, t in nos
               if RE_CELL_NO.match(t.strip()) and not RE_PAGE.match(t)]
        dates = [(y, RE_DATE.search(t)) for y, t in dates
                 if RE_DATE.search(t) and not RE_PAGE.match(t)]
        if len(nos) != len(dates) or not nos:
            continue
        for (_, no), (_, m) in zip(nos, dates):
            y, mo, dd = (int(x) for x in m.groups())
            out.append((no, f"{y:04d}-{mo:02d}-{dd:02d}"))
    return out


# ---------- 通用：把一页的字级坐标还原成表格 ----------

X_CLUSTER = 0.02      # 列聚类的 x 间距（归一化宽度）
X_TOL = 0.06          # 一个观察结果离列起点多远还算这一列
MIN_ROWS = 3
MIN_FILL = 0.6        # 至少六成行有两个以上单元格，才当表格


def _page_grid(obs: list[dict]) -> list[list[str]] | None:
    """一页的观察结果 → 网格。

    行怎么切是难点：单元格折行（同一格占两行）与相邻两行在 y 上只差一个行高，
    不能靠"固定容差"分开。这里用**序号列**做锚：序号每行一个、短、纯数字，
    它的 y 就是行首；其余观察按"落到哪个行首区间"归行，折行自然并进同一行。
    没有序号列就返回 None——宁可少认一张表，也不要把正文切碎。
    """
    if len(obs) < 6:
        return None
    gap = statistics.median(o["h"] for o in obs) or 0.02

    xs = sorted(o["x"] for o in obs)
    cols: list[list[float]] = []
    for x in xs:
        if not cols or x - cols[-1][-1] > X_CLUSTER:
            cols.append([x])
        else:
            cols[-1].append(x)
    starts = [c[0] for c in cols if len(c) >= 2]
    if len(starts) < 2 or len(starts) > 8:
        return None

    def col_of(o: dict) -> int | None:
        j = min(range(len(starts)), key=lambda i: abs(o["x"] - starts[i]))
        return j if abs(o["x"] - starts[j]) <= X_TOL else None

    anchors = sorted(o["y"] for o in obs
                     if col_of(o) == 0 and RE_SEQ.match(o["text"].strip()))
    if len(anchors) < MIN_ROWS - 1:
        return None

    def row_of(y: float) -> int:
        # 离哪个序号最近就归哪行：序号在格子里垂直居中，行首文字常常**高于**序号，
        # 用"y >= 序号"会把下一行的第一行折行并进上一行（2026-11 实测）。
        return min(range(len(anchors)), key=lambda i: abs(y - anchors[i]))

    head_y = [o["y"] for o in obs if o["y"] < anchors[0] - gap * 1.2]
    n_rows = len(anchors) + (1 if head_y else 0)
    grid = [[""] * len(starts) for _ in range(n_rows)]
    offset = 1 if head_y else 0
    for o in obs:
        first = anchors[0] - gap * 1.2
        if o["y"] < first:
            # 表头是居中的，用最近列而不是容差判定：表头文字到列起点的距离常常
            # 超过数据列的容差（「文件名称」居中在 x=0.35，列起点 0.18）。
            j = min(range(len(starts)), key=lambda i: abs(o["x"] - starts[i]))
            grid[0][j] += o["text"]
            continue
        j = col_of(o)
        if j is None:
            continue
        if row_of(o["y"]) < len(anchors):
            grid[row_of(o["y"]) + offset][j] += o["text"]
    grid = [[c.strip() for c in row] for row in grid if any(c.strip() for c in row)]
    if len(grid) < MIN_ROWS:
        return None
    multi = sum(1 for r in grid if sum(1 for c in r if c) >= 2)
    if multi / len(grid) < MIN_FILL:
        return None
    if not header_of(grid):
        return None
    return grid


def tables(pdf_path: str | Path, max_pages: int = 12) -> list[Table]:
    """扫描件的表格：逐页重建网格，返回带页码的 Table（source="ocr"）。"""
    if not HAVE_VISION:
        return []
    obs = observations(pdf_path, max_pages=max_pages)
    if not obs:
        return []
    out: list[Table] = []
    for page in sorted({o["page"] for o in obs}):
        page_obs = [o for o in obs if o["page"] == page and not RE_PAGE_HARD.match(o["text"].strip())]
        grid = _page_grid(page_obs)
        if not grid:
            continue
        xs = [o["x"] for o in page_obs]
        ys = [o["y"] for o in page_obs]
        out.append(Table(rows=grid, page=page + 1,
                         bbox=[min(xs), min(ys), max(xs), max(ys)], source="ocr"))
    return out


def looks_tabular(lines: list[str]) -> bool:
    """OCR 出来的行是不是"表格相"：大量短行、少句末标点。"""
    lines = [ln.strip() for ln in lines if ln.strip()]
    if len(lines) < 8:
        return False
    short = [ln for ln in lines if len(ln) <= 24 and not re.search(r"[。；：，]$", ln)]
    return len(short) / len(lines) >= 0.35
