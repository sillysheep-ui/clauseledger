"""格式体检：五种载体各自"读出来多少、读丢了多少"。

思路：**双读一致性**——同一份字节用两个互相独立的读取器各读一遍，
字数比接近 1 说明没读丢；差得远说明其中一方漏了内容。

  网页 HTML   ours(blocks)             vs  bs4 全文
  原生 PDF    pypdf 逐页                vs  pdfium 逐页
  扫描 PDF    Vision OCR               （无第二读，只报产量与置信度）
  DOCX        python-docx(段落+表格)    vs  textutil
  DOC         textutil                 （无第二读，只报产量）

注意这不是"正确率"：两个读器同时错的地方测不出来。
它只能回答"有没有整块内容没进来"，这正是静默降级最典型的形态。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

DATA = Path("data/processed")
RAW = Path("data/raw")
CJK = re.compile(r"[\u4e00-\u9fa5]")


def cjk(s: str) -> int:
    return len(CJK.findall(s or ""))


def raw_path(sha: str) -> Path | None:
    if not sha:
        return None
    d = RAW / sha[:12]
    if not d.is_dir():
        return None
    files = [p for p in d.iterdir() if p.is_file()]
    return files[0] if files else None


def html_second(data: bytes) -> int:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return cjk(soup.get_text(" "))


def pdf_pypdf(path: Path) -> int:
    from pypdf import PdfReader

    try:
        r = PdfReader(str(path))
        if r.is_encrypted:
            r.decrypt("")
        return cjk("\n".join((p.extract_text() or "") for p in r.pages))
    except Exception:
        return 0


def pdf_pdfium(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
        out = []
        for i in range(len(doc)):
            out.append(doc[i].get_textpage().get_text_range())
        doc.close()
        return cjk("\n".join(out))
    except Exception:
        return 0


def ours_of(path: Path, url: str = "") -> int:
    """用我们自己的解析器读一遍，得到与第二读可比的中文字数。"""
    try:
        from tools.ingest.parse import parse_any

        kind, parsed = parse_any(path.read_bytes(), None, str(path), str(path))
        return cjk("\n".join(b.text for b in parsed.blocks))
    except Exception:
        return 0


def docx_second(path: Path) -> int:
    with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
        r = subprocess.run(["textutil", "-convert", "txt", "-output", tmp.name, str(path)],
                           capture_output=True)
        if r.returncode != 0:
            return 0
        return cjk(Path(tmp.name).read_text("utf-8", errors="ignore"))


def doc_second(path: Path) -> int:
    r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                       capture_output=True)
    return cjk(r.stdout.decode("utf-8", errors="ignore")) if r.returncode == 0 else 0


def ratio(a: int, b: int) -> float:
    return a / b if b else (1.0 if not a else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows: list[dict] = []
    for f in sorted(DATA.glob("*.json")):
        rec = json.loads(f.read_text("utf-8"))
        doc = rec.get("doc_no") or rec.get("title", "?")
        ours = cjk("".join(b["text"] for b in rec.get("blocks") or []))
        kind = rec.get("page_kind")
        pg = raw_path(rec.get("page_sha256", ""))
        if kind == "html" and pg:
            rows.append({"doc": doc, "format": "html", "page": True,
                         "ours": ours, "second": html_second(pg.read_bytes())})
        for att in rec.get("attachments") or []:
            p = raw_path(att.get("sha256", ""))
            if not p:
                continue
            k = att.get("kind") or p.suffix.lstrip(".")
            row = {"doc": doc, "format": k, "page": False, "ours": att.get("chars", 0),
                   "conf": att.get("ocr_conf"), "extract": att.get("extract")}
            if k in ("docx", "doc"):
                row["ours"] = ours_of(p)
            if k == "pdf":
                row["second"] = pdf_pdfium(p)
                row["second_name"] = "pdfium"
                row["first"] = pdf_pypdf(p)
                row["format"] = "pdf-native" if row["first"] > 50 else "pdf-ocr"
            elif k == "docx":
                row["second"] = docx_second(p)
                row["second_name"] = "textutil"
            elif k == "doc":  # 我们的 .doc 通道也用 textutil，第二读不独立，仅报产量
                row["second_name"] = "textutil(同一工具)"
            elif k == "docx":
                row["second"] = doc_second(p)
                row["second_name"] = "textutil"
            else:
                row["second"] = None
            rows.append(row)

    groups = defaultdict(list)
    for r in rows:
        groups[r["format"]].append(r)

    print(f"样本 {len(rows)} 个读取单元（网页 {sum(1 for r in rows if r['page'])} 页 / 附件 {sum(1 for r in rows if not r['page'])} 个）\n")
    head = ("载体", "单元", "零字率", "中位字数", "双读比中位", "双读比<0.6", "最差样本")
    print("{:<11}{:>6}{:>9}{:>10}{:>13}{:>12}  {}".format(*head))
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        g = groups[k]
        ours = [r["ours"] for r in g]
        zero = sum(1 for r in g if r["ours"] == 0) / len(g)
        ratios = []
        for r in g:
            base = r.get("first", r["ours"]) if r["format"].startswith("pdf") else r["ours"]
            sec = r.get("second")
            if sec:
                ratios.append(ratio(base, sec))
        worst = min(g, key=lambda r: ratio(r.get("first", r["ours"]), r["second"]) if r.get("second") else 9)
        wr = ratio(worst.get("first", worst["ours"]), worst["second"]) if worst.get("second") else float("nan")
        print("{:<11}{:>6}{:>8.0%}{:>10}{:>13}{:>12}  {}".format(
            k, len(g), zero, int(statistics.median(ours)),
            f"{statistics.median(ratios):.2f}" if ratios else "—",
            sum(1 for x in ratios if x < 0.6) if ratios else "—",
            f"{worst['doc']} {wr:.2f}"))

    low = [r for r in rows if r.get("second") and ratio(r.get("first", r["ours"]), r["second"]) < 0.6]
    print(f"\n双读比 <0.6 的单元 {len(low)} 个（疑似整块没读进来）")
    for r in low[:12]:
        print(f"  {r['doc']} [{r['format']}] 我们 {r.get('first', r['ours'])} 字 / 第二读 {r['second']} 字")

    if a.json:
        Path("data/reports/formats.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
