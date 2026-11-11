"""扫描件与老格式的兜底：能拿到文本，但要标清楚是"读出来的"还是"认出来的"。

三个兜底：
- PDF 扫描件 → pypdfium2 渲染 + macOS Vision（ocrmac，中文）
- .doc（OLE2）→ macOS 自带 textutil
- 都不行 → 返回 None，由调用方标 unsupported，不许假装读到了
"""
from __future__ import annotations

import subprocess
import threading
import tempfile
from pathlib import Path

OCR_MIN_CONF = 0.5   # 平均置信度低于此值，逐页丢弃
SCAN_PAGE_CHARS = 50  # 单页原生文本少于这个数，判为扫描页


_OCR_GATE = threading.Semaphore(2)   # 全局闸门：Vision 在并发下会互相拖慢，实测 5x3 线程时单份要 1 小时


def ocr_pdf(path: str | Path, workers: int = 2) -> tuple[list[str], float]:
    """渲染（pypdfium2 非线程安全，单线程）+ OCR（Vision 释放 GIL，可多线程）。

    全局闸门 _OCR_GATE 限制同时 OCR 的文档数：实测 5 进程 × 3 线程时
    Vision 会互相拖慢，单份 50 页 PDF 从 30 秒劣化到 1 小时。
    返回 (每页文本, 平均置信度)。
    """
    from concurrent.futures import ThreadPoolExecutor

    import pypdfium2

    from ocrmac import ocrmac

    with _OCR_GATE:
        doc = pypdfium2.PdfDocument(str(path))
        try:
            images = [doc[i].render(scale=2).to_pil() for i in range(len(doc))]
        finally:
            doc.close()

        def one(img) -> tuple[str, list[float]]:
            res = ocrmac.OCR(img, language_preference=["zh-Hans"]).recognize()
            keep = [(t, c) for t, c, _ in res if c >= OCR_MIN_CONF]
            return "\n".join(t for t, _ in keep), [c for _, c in keep]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            out = list(pool.map(one, images))

    pages = [t for t, _ in out]
    confs = [c for _, cs in out for c in cs]
    return pages, (sum(confs) / len(confs) if confs else 0.0)


def doc_to_text(path: str | Path) -> str | None:
    """.doc（OLE2）→ 纯文本，用 macOS 自带 textutil；非 macOS 或失败返回 None。"""
    try:
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                           capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def write_temp(data: bytes, suffix: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return Path(f.name)
