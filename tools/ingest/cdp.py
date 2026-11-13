"""真 Chrome 兜底：用用户装的正版 Chrome，通过 CDP 取页面。

为什么需要第三档：
    httpx         → 412 / 521（一眼被认出不是浏览器）
    Playwright Chromium → 挑战脚本循环，0 字（指纹仍是自动化）
    真 Chrome     → 正常渲染（实测成都 1,551 字）

差异不在 IP，在**客户端身份**。所以对付"挑战式防护"（瑞数/加速乐/CT2），
唯一稳的办法是让真的浏览器去访问，我们只在旁边通过 CDP 读结果。

代价与取舍：
  - 必须 `--no-sandbox`：本机进程被外层沙箱限制，Chrome 自己的 sandbox 起不来。
    这只适合"本机/受信环境"，不能带到生产。
  - 用一个临时 profile，不碰用户正在用的 Chrome。
  - 慢（起浏览器 3–8 秒），只对 412/521 这类状态码触发。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

RAW = Path("data/raw")


def cjk(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa5]", text))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PORT = 9222


def _alive(port: int = DEFAULT_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_browser(port: int = DEFAULT_PORT, profile: str = "/tmp/clauseledger-chrome") -> None:
    """起一个独立的真 Chrome（临时 profile），不动用户正在用的那个。"""
    if _alive(port):
        return
    Path(profile).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--no-sandbox",
         "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    for _ in range(40):
        if _alive(port):
            return
        time.sleep(0.5)
    raise RuntimeError("real chrome did not start (CDP port not listening)")


def _kill(port: int = DEFAULT_PORT) -> None:
    try:
        subprocess.run(["pkill", "-f", f"remote-debugging-port={port}"], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.0)


def fetch_with_real_chrome(url: str, *, wait: float = 5.0, port: int = DEFAULT_PORT,
                           min_chars: int = 200, retry: int = 1) -> tuple[int, bytes, str]:
    """返回 (status, content, content_type)。没拿到正文就抛。

    见过的一种崩法：上一次 CDP 连接断开后，Chrome 停在"context 管理不可用"的状态
    （`Browser.setDownloadBehavior ... not supported`）。所以失败重启浏览器再试一次。
    """
    from playwright.sync_api import sync_playwright

    last: Exception | None = None
    for attempt in range(retry + 1):
        if attempt:
            _kill(port)
        ensure_browser(port)
        try:
            with sync_playwright() as pw:
                br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                ctx = br.contexts[0] if br.contexts else br.new_context()
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                resp = pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
                status = resp.status if resp else 0
                pg.wait_for_timeout(int(wait * 1000))
                if cjk(pg.content()) < min_chars:        # 第二段：挑战跑完再要一次
                    try:
                        pg.reload(wait_until="domcontentloaded", timeout=60_000)
                    except Exception:                   # noqa: BLE001
                        pass
                    pg.wait_for_timeout(int(wait * 500))
                html = pg.content()
                try:
                    pg.close()
                except Exception:                       # noqa: BLE001
                    pass
            if cjk(html) < min_chars:
                raise RuntimeError(f"real chrome got {len(html)} bytes but only "
                                   f"{cjk(html)} 汉字 (status={status})")
            # 首包是挑战页（412），跑完 JS 后拿到的是真正文 → 记 200
            return 200, html.encode("utf-8"), "text/html; charset=utf-8"
        except Exception as e:  # noqa: BLE001
            last = e
    raise last if last else RuntimeError("real chrome failed")


def real_chrome_fetch(url: str, *, pause: float = 1.0, **kw) -> tuple[int, bytes, str, str, Path]:
    status, content, ctype = fetch_with_real_chrome(url, **kw)
    h = hashlib.sha256(content).hexdigest()
    d = RAW / h[:12]
    d.mkdir(parents=True, exist_ok=True)
    p = d / (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html")
    p.write_bytes(content)
    time.sleep(pause)
    return status, content, ctype, h, p


def info(port: int = DEFAULT_PORT) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
        return json.loads(r.read())
