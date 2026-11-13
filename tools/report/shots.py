"""给核对用：把公文原文的"落款"和列表页那一行截成图。

用法：
    python -m tools.report.shots --doc 沪人社信发〔2016〕21号
    python -m tools.report.shots --pdf 沪教委学〔2026〕13号 --page 4
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

OUT = Path("docs/scan/shots")


def load(doc_no: str) -> dict:
    for p in Path("data/processed").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("doc_no") == doc_no:
            return d
    raise SystemExit(f"没找到 {doc_no}，先跑 python -m tools.ingest.registry")


def shot_html(url: str, out: Path, match: str) -> bool:
    """打开详情页，截取包含 match 文本的那个容器（通常是正文 div）。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1100, "height": 1400})
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(1200)
        try:
            el = pg.locator(f"text={match}").last
            el.scroll_into_view_if_needed()
            box = pg.locator("body").bounding_box()
            # 截取落款附近一块：以命中元素为中心，向上 260px、向下 120px
            eb = el.bounding_box()
            if eb:
                top = max(0, eb["y"] - 260)
                pg.screenshot(
                    path=str(out),
                    clip={"x": 0, "y": top, "width": min(1100, (box or {}).get("width", 1100)),
                          "height": min(420, eb["y"] + eb["height"] + 120 - top)},
                )
                br.close()
                return True
        except Exception as exc:  # noqa: BLE001
            print("  截图失败:", exc)
        pg.screenshot(path=str(out), full_page=False)
        br.close()
        return False


def shot_pdf(url: str, out: Path, page_no: int) -> None:
    import pypdfium2 as pdfium

    raw = OUT / "_tmp.pdf"
    import httpx

    raw.write_bytes(httpx.get(url, timeout=60, headers={"User-Agent": "ClauseLedger/0.1"}).content)
    doc = pdfium.PdfDocument(str(raw))
    page = doc[page_no - 1]
    img = page.render(scale=2).to_pil()
    img.save(out)
    doc.close()
    raw.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    ap.add_argument("--pdf")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--match", default="")
    ap.add_argument("--list-row", help="截列表页里含这个文号的一行")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.list_row:
        from playwright.sync_api import sync_playwright

        out = OUT / f"list-{args.list_row}.png"
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={"width": 1100, "height": 900})
            pg.goto("https://rsj.sh.gov.cn/tgwgfx_17726/index.html",
                    wait_until="domcontentloaded", timeout=45000)
            row = pg.locator("tr", has_text=args.list_row).first
            row.screenshot(path=str(out))
            br.close()
        print("→", out)
        return 0

    if args.pdf:
        d = load(args.pdf)
        out = OUT / f"{args.pdf}-p{args.page}.png"
        shot_pdf(d["url"], out, args.page)
        print("→", out)
        return 0

    if args.doc:
        d = load(args.doc)
        out = OUT / f"{args.doc}.png"
        ok = shot_html(d["url"], out, args.match or "上海市")
        print("→", out, "(命中落款附近)" if ok else "(整页)")
        return 0

    print("给参数：--doc / --pdf / --list-row")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
