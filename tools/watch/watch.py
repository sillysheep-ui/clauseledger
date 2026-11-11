"""变更监测（L1）：抓栏目表格 → 存快照 → 两次快照 diff 出 ChangeEvent。

只做一件事：把"有没有新文件 / 有没有改"变成可判定的数据。
不解析正文、不判定有效性 —— 那是 ingest 阶段的事。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

UA = "ClauseLedger-M0/0.1 (policy watch; contact: liuduo_sky@163.com)"
DELAY = 1.0  # 每请求间隔，礼貌抓取


@dataclass
class Item:
    doc_no: str
    title: str
    issued_at: str
    valid_until: str
    url: str


@dataclass
class Snapshot:
    column: str
    fetched_at: str
    pages: int
    items: list[Item] = field(default_factory=list)

    def digest(self) -> str:
        h = hashlib.sha256()
        for it in sorted(self.items, key=lambda x: (x.doc_no, x.url)):
            h.update(f"{it.doc_no}|{it.title}|{it.issued_at}|{it.valid_until}".encode())
        return h.hexdigest()[:16]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_rows(html: str, base: str) -> list[Item]:
    """栏目页表格：标题 | 文号 | 生成日期 | 有效期至。列数不符时降级按位置取。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Item] = []
    for tr in soup.select("table tbody tr"):
        a = tr.find("a", href=True)
        if not a:
            continue
        tds = [_norm(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        title = _norm(a.get("title") or a.get_text(" ", strip=True))
        href = a["href"]
        url = href if href.startswith("http") else urljoin(base, href)
        doc_no = tds[1] if len(tds) > 1 else ""
        issued = tds[2] if len(tds) > 2 else ""
        valid = tds[3] if len(tds) > 3 else ""
        if not re.match(r"20\d\d-\d\d-\d\d", issued):      # 兼容列顺序变化
            cand = [t for t in tds[1:] if re.match(r"20\d\d-\d\d-\d\d", t)]
            issued = cand[0] if cand else ""
            valid = cand[1] if len(cand) > 1 else ""
        out.append(Item(doc_no=doc_no, title=title, issued_at=issued, valid_until=valid, url=url))
    return out


def fetch_column(base: str, max_pages: int | None = None, client: httpx.Client | None = None) -> Snapshot:
    """翻完 totalPage（页脚 JS 里有），默认全量。"""
    own = client is None
    cli = client or httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    try:
        first = cli.get(base if base.endswith(".html") else base.rstrip("/") + "/index.html")
        first.raise_for_status()
        m = re.search(r"totalPage\s*:\s*(\d+)", first.text)
        total = int(m.group(1)) if m else 1
        pages = min(total, max_pages) if max_pages else total
        items = parse_rows(first.text, base)
        for p in range(2, pages + 1):
            time.sleep(DELAY)
            u = re.sub(r"index(_\d+)?\.html$", f"index_{p}.html", first.url.path) if first.url.path.endswith(".html") else base.rstrip("/") + f"/index_{p}.html"
            r = cli.get(str(first.url.copy_with(path=u)))
            if r.status_code != 200:
                break
            items += parse_rows(r.text, base)
        return Snapshot(column=base, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        pages=pages, items=items)
    finally:
        if own:
            cli.close()


def diff(old: Snapshot, new: Snapshot) -> list[dict]:
    """以文号为键；无文号退化为 URL。只报 added/revised/removed 三类。"""
    def key(it: Item) -> str:
        return it.doc_no or it.url

    o = {key(i): i for i in old.items}
    n = {key(i): i for i in new.items}
    events: list[dict] = []
    for k, it in n.items():
        if k not in o:
            events.append({"action": "added", "doc_no": it.doc_no, "title": it.title, "url": it.url})
        elif asdict(it) != asdict(o[k]):
            changed = [f for f in ("title", "issued_at", "valid_until") if getattr(it, f) != getattr(o[k], f)]
            events.append({"action": "revised", "doc_no": it.doc_no, "title": it.title,
                           "fields": changed, "url": it.url})
    for k, it in o.items():
        if k not in n:
            events.append({"action": "removed", "doc_no": it.doc_no, "title": it.title, "url": it.url})
    return events


def save(snap: Snapshot, root: Path) -> Path:
    d = root / re.sub(r"[^a-z0-9]+", "-", snap.column.split("//")[-1].lower()).strip("-")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{snap.fetched_at.replace(':', '')}.json"
    p.write_text(json.dumps({"column": snap.column, "fetched_at": snap.fetched_at, "pages": snap.pages,
                             "digest": snap.digest(), "items": [asdict(i) for i in snap.items]},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p


if __name__ == "__main__":
    import sys

    base = sys.argv[1] if len(sys.argv) > 1 else "https://rsj.sh.gov.cn/tgwgfx_17726/index.html"
    mp = int(sys.argv[2]) if len(sys.argv) > 2 else None
    snap = fetch_column(base, max_pages=mp)
    path = save(snap, Path("data/watch"))
    print(f"{snap.column}\n  {snap.pages} 页 / {len(snap.items)} 条 / digest={snap.digest()} -> {path}")
    for it in snap.items[:5]:
        print(f"  - {it.doc_no:<18} {it.issued_at} 至 {it.valid_until or '长期':<12} {it.title[:40]}")
