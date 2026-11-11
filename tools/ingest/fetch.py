"""礼貌抓取：限速 1 req/s、重试、按内容哈希落盘。

落盘不是缓存，是证据：审计要能回答"当时拿到的是哪一份字节"。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

UA = ("ClauseLedger/0.1 (+https://github.com/sillysheep-ui/clauseledger; "
      "contact: liuduo_sky@163.com)")
RAW = Path("data/raw")


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes
    content_type: str
    sha256: str
    path: Path | None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, *, referer: str | None = None, pause: float = 1.0) -> Fetched:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.get(url, headers=headers, timeout=25, follow_redirects=True)
            if r.status_code < 400:
                h = sha(r.content)
                d = RAW / h[:12]
                d.mkdir(parents=True, exist_ok=True)
                p = d / (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html")
                p.write_bytes(r.content)
                time.sleep(pause)
                return Fetched(url, r.status_code, r.content,
                               r.headers.get("content-type", ""), h, p)
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(pause * (2**attempt))
    raise RuntimeError(f"fetch failed {url}: {last}")
