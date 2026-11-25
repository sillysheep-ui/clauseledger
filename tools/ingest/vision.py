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

try:  # 扫描件的表格线检测：有 cv2 就用，没有就退回序号锚
    import cv2

    HAVE_CV = True
except Exception:  # pragma: no cover
    HAVE_CV = False

from tools.ingest.tables import Table, header_of

RE_DOCNO = re.compile(
    r"沪\s*[\u4e00-\u9fa5]{1,8}\s*[〔（(]\s*\d{2,4}\s*[〕）)]\s*第?\s*\d+\s*号"
)
RE_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 序号列：允许 OCR 把 0/1 认成 O/I/l/|（实测「10」「11」常被认成「IO」「I1」），
# 只在这一列上放宽；仍然要求后面能把它们归一成整数。
RE_SEQ = re.compile(r"^[0-9IlLOo|]{1,3}$")


def _seq_value(text: str) -> int | None:
    t = (text or "").strip()
    if not RE_SEQ.match(t):
        return None
    t = t.replace("I", "1").replace("l", "1").replace("L", "1").replace("O", "0").replace("o", "0").replace("|", "1")
    return int(t) if t.isdigit() else None
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

    seqs = [(o["y"], _seq_value(o["text"])) for o in obs if col_of(o) == 0]
    seqs = [(y, v) for y, v in seqs if v is not None]
    # 序号必须递增；乱序的丢掉（OCR 噪声），避免把正文里的数字当行锚
    anchors, last = [], 0
    for y, v in sorted(seqs):
        if v > last:
            anchors.append(y); last = v
    if len(anchors) < MIN_ROWS - 1:
        return None

    # Vision 偶尔整行漏认（实测「10」直接没输出，行内容被并进上一行/下一行）。
    # 用行距的中位数补空心锚：间隔接近整数倍行距就插回缺失的行位。
    if len(anchors) >= 3:
        # 行距：多数间隔就是一个行距，漏认的行会让某个间隔变成 2 倍。
        # 取中位数；只有当"最小/中位"差得远（说明中位被漏行抬高）时才用最小值。
        diffs = [b - a for a, b in zip(anchors, anchors[1:])]
        med, mn = statistics.median(diffs), min(diffs)
        pitch = mn if med / mn > 1.4 else med
        fixed = list(anchors)
        for a, b in zip(anchors, anchors[1:]):
            k = round((b - a) / pitch)
            if k >= 2 and abs((b - a) / pitch - k) <= 0.35:
                fixed += [a + (b - a) * j / k for j in range(1, k)]
        anchors = sorted(fixed)
    if len(anchors) < MIN_ROWS - 1:
        return None

    def row_of(y: float) -> int:
        # 离哪个序号最近就归哪行：序号在格子里垂直居中，行首文字常常**高于**序号，
        # 用"y >= 序号"会把下一行的第一行折行并进上一行（2026-11 实测）。
        return min(range(len(anchors)), key=lambda i: abs(y - anchors[i]))

    # 表头只取紧贴第一行上方的那一带：再往上是大标题（「附件…目录」），
    # 把它并进表头会让整表的第一列永远带一串标题文字。
    # 表头在第一个行锚上方约 2 个行距处；贴得太近会把首行的折行当表头，
    # 太远会把大标题并进来（「附件…目录序号」）。
    head_y = [o["y"] for o in obs
              if anchors[0] - gap * 3.5 <= o["y"] < anchors[0] - gap * 1.4]
    n_rows = len(anchors) + (1 if head_y else 0)
    grid = [[""] * len(starts) for _ in range(n_rows)]
    offset = 1 if head_y else 0
    for o in obs:
        first = anchors[0] - gap * 3.5
        if o["y"] < first:
            continue
        if o["y"] < anchors[0] - gap * 1.4:
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
    _merge_fragment_rows(grid)
    _renumber(grid)
    return grid


FOOTER = re.compile(r"(印发|此件主动公开|^\s*[—\-一]\s*\d+\s*[—\-一]\s*$|^\s*$)")
FOOTER_WORDS = re.compile(r"印发|主动公开|上海市.{0,12}办公室")


def _merge_fragment_rows(grid: list[list[str]]) -> None:
    """把"被行锚切开的半行"并回去，并丢掉表尾的落款/页码行。

    高行的格子会折很多行，序号却只在行中居中：最近锚归行时，前几行字可能落到
    上一行、后几行字落到本行，出现"序号有、文号/日期空"的半行。判据很简单：
    序号是数字、且文号/日期列有空、下一行有——就把下一行并上来。
    """
    i = 1
    while i < len(grid) - 1:
        cur, nxt = grid[i], grid[i + 1]
        if _seq_value(cur[0]) is not None and any(not c for c in cur[1:]):
            if any(nxt[1:]) and _seq_value(nxt[0]) is None:
                for j in range(1, len(cur)):
                    cur[j] = (cur[j] + nxt[j]).strip() if cur[j] else nxt[j]
                del grid[i + 1]
                continue
        i += 1
    # 表尾：从后往前删落款/页码行（没有序号、也没有文号形态的）
    while len(grid) > 2:
        last = grid[-1]
        text = "".join(last)
        if _seq_value(last[0]) is None and (FOOTER_WORDS.search(text) or not text.strip()):
            grid.pop()
        else:
            break


def _renumber(grid: list[list[str]]) -> None:
    """把序号列按顺序补齐。

    Vision 会漏认整行（「10」没输出）或把「11」认成「I1」。只要这一列过半是数字，
    就按顺序填：空白的补上，含 I/O/l/| 的纠正——**只动这一列**，其它列原样。
    """
    vals = [_seq_value(r[0]) for r in grid]
    known = [v for v in vals[1:] if v is not None]
    if not known or len(known) / max(1, len(grid) - 1) < 0.6:
        return
    start = known[0] - (vals[1:].index(known[0]) if known[0] in vals[1:] else 0)
    for i in range(1, len(grid)):
        want = start + i - 1
        got = vals[i]
        raw = grid[i][0].strip()
        if got == want:
            if re.search(r"[IlLOo|]", raw):
                grid[i][0] = str(want)
            continue
        if not raw or re.search(r"[IlLOo|]", raw) or (got is not None and abs(got - want) <= 1):
            grid[i][0] = str(want)


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
        # 两套读法都算，谁读到的行多就用谁：线网格边界客观但会漏掉最后一条边线，
        # 序号锚靠行数补，正好互补（9 号页：线网格 4 行 vs 序号锚 5 行）。
        anchor = _page_grid(page_obs)
        line = None
        try:
            cand = _line_grid(_page_image(pdf_path, page), page_obs)
            line = cand if cand and _grid_is_sane(cand) else None
        except Exception:
            line = None
        # 默认仍用序号锚：实测在真实页面上线网格会把 20 号的最后两行并成一格
        # （少一条竖线就串列），只在序号锚完全失败时才用它兜底。
        grid = anchor or line
        if not grid:
            continue
        grid = _clean_grid(grid)
        if not grid:
            continue
        xs = [o["x"] for o in page_obs]
        ys = [o["y"] for o in page_obs]
        out.append(Table(rows=grid, page=page + 1,
                         bbox=[min(xs), min(ys), max(xs), max(ys)], source="ocr"))
    return out


def _max_run(row) -> int:
    """一行里最长的连续 True 段——表格线是**连续**的，文字行会被字间空隙打断。"""
    best = cur = 0
    for v in row:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def _line_positions(mask, axis: int, ratio: float = 0.3) -> list[float]:
    """把"很长的横线/竖线"从二值图里找出来，返回它们的位置（像素）。

    判据用**最长连续段**而不是像素总数：文字行里横笔很多、总数也能过线，
    但连续段很短（2026-11 实测 6 号页把 50 个文字行误判成横线）。
    """
    flags = mask.astype(bool)
    rows = flags if axis == 1 else flags.T
    total = mask.shape[1 - axis]
    hot = [_max_run(r) > total * ratio for r in rows]
    out, run = [], []
    for i, flag in enumerate(hot):
        if flag:
            run.append(i)
        elif run:
            out.append(sum(run) / len(run)); run = []
    if run:
        out.append(sum(run) / len(run))
    # 一条 3px 的线在二值图上会出现两条边（x、x+2），不合并就会多出整列。
    merged: list[float] = []
    for v in out:
        if merged and v - merged[-1] <= 6:
            merged[-1] = (merged[-1] + v) / 2
        else:
            merged.append(v)
    return merged


def _line_grid(image, obs: list[dict]) -> list[list[str]] | None:
    """用**表格线**重建网格：扫描件里有框线时，行边界是客观存在的。

    为什么需要它：序号锚靠"一行一个序号"推边界，遇到很高的行（一格折 5–6 行、
    序号垂直居中）会把一行切开——文号落到下一行、日期还在本行，配对就散了。
    有横竖线时直接取交点，行边界不再靠猜。
    """
    if not HAVE_CV or not obs:
        return None
    import numpy as np

    arr = np.array(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 15, 2)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 30), 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 30))))
    ys = _line_positions(horiz, axis=1, ratio=0.3)
    xs = _line_positions(vert, axis=0, ratio=0.3)
    # 底边常被漏掉（最后一条横线压在文字上或太淡）：竖线一直延伸到哪，
    # 就说明表格画到哪，用它把缺失的上下边界补回来。
    if len(ys) >= 2 and len(xs) >= 2:
        import numpy as _np
        rows_with_vert = _np.where(vert.any(axis=1))[0]
        if rows_with_vert.size:
            top, bot = int(rows_with_vert[0]), int(rows_with_vert[-1])
            pitch = statistics.median([b - a for a, b in zip(ys, ys[1:])]) or h / 20
            if ys[0] - top > pitch * 0.5:
                ys.insert(0, top)
            if bot - ys[-1] > pitch * 0.5:
                ys.append(bot)
    if len(ys) < 4 or len(xs) < 3:
        return None
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    if n_rows > 60 or n_cols > 8:
        return None

    grid = [[""] * n_cols for _ in range(n_rows)]
    for o in obs:
        cx, cy = o["x"] * w, o["y"] * h
        col = next((j for j in range(n_cols) if xs[j] <= cx < xs[j + 1]), None)
        row = next((i for i in range(n_rows) if ys[i] <= cy < ys[i + 1]), None)
        if col is None or row is None:
            continue
        grid[row][col] = (grid[row][col] + " " + o["text"]).strip() if grid[row][col] else o["text"]
    grid = [r for r in grid if any(c.strip() for c in r)]
    if len(grid) < 2:
        return None
    return grid


RE_FOOTER = re.compile(r"[（(]\s*此件主动公开\s*[)）]|[—\-一]\s*\d{1,3}\s*[—\-一]")
RE_TAIL_MARK = re.compile(r"[—\-一]\s*\d{1,3}\s*$")


def _clean_grid(grid: list[list[str]]) -> list[list[str]]:
    """把页脚从句子里剥掉，再丢掉被页脚撑出来的空行。

    扫描页的「（此件主动公开）」「— 2 —」和落款会被 Vision 认进最后一个单元格；
    不剥掉的话，最后一行的序号会变成「6（此件主动公开）」，`RE_SEQ` 匹配不上，
    整行就被当作无序号行丢掉（9 号页因此少一行、10 号少一条边）。
    """
    out: list[list[str]] = []
    for r in grid:
        cells = []
        for j, c in enumerate(r):
            t = RE_FOOTER.sub("", c or "") if j else RE_TAIL_MARK.sub("", RE_FOOTER.sub("", c or ""))
            # 文号格子尾巴常粘一个页码数字：「沪人社规（2019）40号2」
            if j and re.search(r"号\s*\d{1,3}$", t):
                t = re.sub(r"(号)\s*\d{1,3}$", r"\1", t)
            cells.append(t.strip(" 　"))
        if not any(cells):
            continue
        if not cells[0] and sum(1 for c in cells[1:] if c) < 2:
            continue
        out.append(cells)
    return out


def _grid_is_sane(grid: list[list[str]]) -> bool:
    """线网格的质量闸：行数要和序号列的数量对得上。

    扫描件的线检测会被文字基线骗（6 号页曾把一行字切成一行"表"），闸门是：
    第一列如果是序号，行数必须≈序号个数（±1）；否则要求多数行有两个以上单元格。
    """
    if len(grid) < 2:
        return False
    seqs = [v for v in (_seq_value(r[0]) for r in grid[1:]) if v is not None]
    if len(seqs) >= 3:
        return abs(len(grid) - 1 - len(seqs)) <= 1
    filled = sum(1 for r in grid[1:] if sum(1 for c in r if c.strip()) >= 2)
    return filled >= (len(grid) - 1) * 0.6


def _page_image(pdf_path: str | Path, page: int, scale: float = 2):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    return pdf[page].render(scale=scale).to_pil()


def looks_tabular(lines: list[str]) -> bool:
    """OCR 出来的行是不是"表格相"：大量短行、少句末标点。"""
    lines = [ln.strip() for ln in lines if ln.strip()]
    if len(lines) < 8:
        return False
    short = [ln for ln in lines if len(ln) <= 24 and not re.search(r"[。；：，]$", ln)]
    return len(short) / len(lines) >= 0.35
