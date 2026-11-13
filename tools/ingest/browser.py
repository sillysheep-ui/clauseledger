"""浏览器兜底：httpx 拿不到时，用 Chromium 跑一遍两段式挑战。

为什么要它：有些站点第一次请求只回一小段混淆 JS（HTTP 412/521），
浏览器执行脚本、写入 cookie、再请求一次才给正文。httpx 不执行 JS，
所以它看到的永远是那个壳——**这不是"被拒绝"，是"没完成握手"**。

两段式的具体做法：
    1. goto(url)，等 wait 秒，让挑战脚本跑完
    2. 如果正文还是空的，reload 一次（这时已带 cookie）
    3. 判断标准不是状态码，是"有没有正文"
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

RAW = Path("data/raw")

# 浏览器装在仓库里（~/Library/Caches 在本机被沙箱拒），用相对路径省得每次 export
LOCAL_BROWSERS = Path(".pw-browsers")
if LOCAL_BROWSERS.exists() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(LOCAL_BROWSERS.resolve())

# 这些状态码表明"对方在考验客户端"，值得换浏览器再试
CHALLENGE_STATUS = {403, 412, 418, 429, 503, 521, 522, 524}


def should_try_browser(status: int | None) -> bool:
    """None = 网络层异常，浏览器也救不了，不必试。"""
    return status in CHALLENGE_STATUS


def cjk(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa5]", text))


def substantive(html: str, min_chars: int = 200) -> bool:
    """判据不是"字节够不够"，是"有没有正文"。

    踩过的坑：7 KB 的挑战页也能过 200 字节，但中文字只有 5 个。
    """
    return cjk(html) >= min_chars


def _save(content: bytes, url: str) -> tuple[str, Path]:
    h = hashlib.sha256(content).hexdigest()
    d = RAW / h[:12]
    d.mkdir(parents=True, exist_ok=True)
    p = d / (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html")
    p.write_bytes(content)
    return h, p


def fetch_with_browser(url: str, *, wait: float = 5.0, settle: float = 2.0,
                       min_chars: int = 200) -> tuple[int, bytes, str]:
    """返回 (status, content, content_type)。拿不到正文就抛 RuntimeError。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        try:
            pg = br.new_page(viewport={"width": 1280, "height": 900})
            resp = pg.goto(url, wait_until="domcontentloaded", timeout=45_000)
            status = resp.status if resp else 0
            pg.wait_for_timeout(int(wait * 1000))
            html = pg.content()
            if not substantive(html, min_chars):   # 第二段：带 cookie 再来一次
                try:
                    pg.reload(wait_until="domcontentloaded", timeout=45_000)
                except Exception:              # noqa: BLE001 挑战脚本会自己跳转
                    pass
                pg.wait_for_timeout(int(settle * 1000))
                html = pg.content()
            if not substantive(html, min_chars):
                raise RuntimeError(f"browser got {len(html)} bytes but only "
                                   f"{cjk(html)} 汉字 (status={status})")
            # 首包可能是挑战页（4xx），但最终拿到了正文 → 这一趟是成功的
            return 200, html.encode("utf-8"), "text/html; charset=utf-8"
        finally:
            br.close()


def browser_fetch(url: str, *, pause: float = 1.0, **kw) -> tuple[int, bytes, str, str, Path]:
    """浏览器抓取并落盘，返回 (status, content, content_type, sha256, path)。"""
    status, content, ctype = fetch_with_browser(url, **kw)
    h, p = _save(content, url)
    time.sleep(pause)
    return status, content, ctype, h, p
