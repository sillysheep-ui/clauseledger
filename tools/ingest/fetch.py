"""礼貌抓取：限速 1 req/s、按状态码决定是否重试、按内容哈希落盘。

落盘不是缓存，是证据：审计要能回答"当时拿到的是哪一份字节"。

重试策略（重点）：**不是所有失败都值得重试**。
  403/404/410/412/451 是确定性结果——重试只会白等，还像在攻击对方。
  408/429/5xx 才是"稍后可能好"，重试有意义。
  网络异常（超时、连接重置）也算可重试。
"""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

UA = ("ClauseLedger/0.1 (+https://github.com/sillysheep-ui/clauseledger; "
      "contact: liuduo_sky@163.com)")
RAW = Path("data/raw")
MAX_ATTEMPTS = 3

# 可重试：稍后可能就正常了
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 521, 522, 524}


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes
    content_type: str
    sha256: str
    path: Path | None
    attempts: int = 1


class FetchError(RuntimeError):
    """抓取失败，但把"为什么失败"结构化地带出去。"""

    def __init__(self, url: str, *, status: int | None, attempts: int, last: Exception | str):
        self.url = url
        self.status = status
        self.attempts = attempts
        self.last = str(last)[:200]
        super().__init__(f"fetch failed {url} (status={status}, attempts={attempts}): {self.last}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def retryable(status: int | None) -> bool:
    """None 表示网络异常（没拿到状态码），按可重试处理。"""
    return status is None or status in RETRYABLE_STATUS


def fetch(url: str, *, referer: str | None = None, pause: float = 1.0,
          attempts: int = MAX_ATTEMPTS) -> Fetched:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer

    status: int | None = None
    last: Exception | str = ""
    for attempt in range(1, attempts + 1):
        try:
            r = httpx.get(url, headers=headers, timeout=25, follow_redirects=True)
            status = r.status_code
            if status < 400:
                h = sha(r.content)
                d = RAW / h[:12]
                d.mkdir(parents=True, exist_ok=True)
                p = d / (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html")
                p.write_bytes(r.content)
                time.sleep(pause)
                return Fetched(url, status, r.content,
                               r.headers.get("content-type", ""), h, p, attempt)
            last = f"HTTP {status}"
            if not retryable(status):
                raise FetchError(url, status=status, attempts=attempt, last=last)
        except FetchError:
            raise
        except Exception as e:  # noqa: BLE001  网络层异常
            status = None
            last = e
        if attempt < attempts:
            time.sleep(pause * (2 ** (attempt - 1)) * (1 + random.random() * 0.2))
    raise FetchError(url, status=status, attempts=attempts, last=last)
