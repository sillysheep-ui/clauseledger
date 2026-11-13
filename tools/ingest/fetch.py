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
    via: str = "httpx"          # httpx | browser


class FetchError(RuntimeError):
    """抓取失败，但把"为什么失败"结构化地带出去。"""

    def __init__(self, url: str, *, status: int | None, attempts: int, last: Exception | str,
                 stage: str = "httpx"):
        self.url = url
        self.status = status
        self.attempts = attempts
        self.stage = stage
        self.last = str(last)[:200]
        super().__init__(f"fetch failed {url} (status={status}, attempts={attempts}, "
                         f"stage={stage}): {self.last}")


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


def fetch_smart(url: str, *, allow_browser: bool = True, allow_chrome: bool = True,
                **kw) -> Fetched:
    """三档升级：httpx → 无头 Chromium → 真 Chrome（CDP）。

    为什么要三档：同一份页面，三种客户端拿到的结果不同，说明拦的**不是 IP，是客户端身份**。
      httpx      快、可批量，但一眼被认出不是浏览器（412/521）。
      Chromium   真跑 JS，能过一部分挑战；但指纹仍是自动化，瑞数/加速乐会循环。
      真 Chrome  指纹是真的，实测成都、兰州可取；代价是必须 --no-sandbox，只在本机用。

    失败时要能说清"卡在哪一档"，所以 stage 一路带出去：
      stage="httpx"   —— 连浏览器都没试（不可重试的状态码，或关掉了）
      stage="browser" —— 两档浏览器都失败，这才是真的拿不到
    """
    from tools.ingest.browser import browser_fetch, should_try_browser

    try:
        return fetch(url, **kw)
    except FetchError as e:
        if not allow_browser or not should_try_browser(e.status):
            raise
        errs = [f"httpx={e.status}"]
        try:
            status, content, ctype, h, p = browser_fetch(url, pause=kw.get("pause", 1.0))
            return Fetched(url, status, content, ctype, h, p, attempts=1, via="browser")
        except Exception as be:  # noqa: BLE001
            errs.append(f"browser={str(be)[:100]}")

        if allow_chrome:
            try:
                from tools.ingest.cdp import real_chrome_fetch
                status, content, ctype, h, p = real_chrome_fetch(url, pause=kw.get("pause", 1.0))
                return Fetched(url, status, content, ctype, h, p, attempts=1, via="chrome")
            except Exception as ce:  # noqa: BLE001
                errs.append(f"chrome={str(ce)[:100]}")

        raise FetchError(url, status=e.status, attempts=e.attempts, stage="browser",
                         last="; ".join(errs)) from e
