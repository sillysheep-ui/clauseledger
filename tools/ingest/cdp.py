"""真 Chrome 兜底：用用户装的正版 Chrome 取页面。

为什么需要这一档（2026-11-13 实测，同一 URL 同一台机器）：
    httpx              → 412，0 汉字（不执行 JS）
    Playwright Chromium→ 挑战循环，0 字（指纹是自动化）
    Playwright + channel="chrome" → 仍然 0 字（Playwright 会打上自动化标记）
    真 Chrome + CDP    → 360 KB，正常渲染

差别不在 IP，在**客户端身份**。所以办法是：让一个"干净的"真 Chrome 去访问，
我们只在旁边通过 CDP 读结果。

三个必须记住的坑：
  1. 必须 `--no-sandbox`：外层沙箱下 Chrome 自己的 sandbox 起不来
     （日志 `sandbox initialization failed: Operation not permitted`）。
     只适合本机受信环境，不能带到生产。
  2. `Browser.setDownloadBehavior ... not supported`：复用一个跑过头的 Chrome 会这样崩。
     解决办法不是重试，是**每次拉一个新的**，用完 terminate。
  3. 端口不要写死：每次挑空闲端口 + 独立 profile，避免和用户自己的 Chrome、和上一次的僵尸打架。
"""
from __future__ import annotations

import hashlib
import re
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

RAW = Path("data/raw")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def cjk(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa5]", text))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _launch(port: int, profile: str) -> subprocess.Popen:
    return subprocess.Popen(
        [CHROME, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "--no-sandbox",
         "--disable-gpu", "--disable-extensions", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def fetch_with_real_chrome(url: str, *, wait: float = 6.0, min_chars: int = 200
                           ) -> tuple[int, bytes, str]:
    """开一个全新的真 Chrome，取一页，关掉。返回 (200, content, content_type)。"""
    from playwright.sync_api import sync_playwright

    port = _free_port()
    profile = tempfile.mkdtemp(prefix=f"cl-chrome-{port}-")
    proc = _launch(port, profile)
    try:
        for _ in range(60):
            if _alive(port):
                break
            if proc.poll() is not None:
                raise RuntimeError("chrome exited during startup")
            time.sleep(0.5)
        else:
            raise RuntimeError("chrome CDP port never came up")

        with sync_playwright() as pw:
            br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = br.contexts[0] if br.contexts else br.new_context()
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
            pg.wait_for_timeout(int(wait * 1000))
            html = pg.content()
            if cjk(html) < min_chars:                # 第二段：挑战跑完再要一次
                try:
                    pg.reload(wait_until="domcontentloaded", timeout=60_000)
                    pg.wait_for_timeout(int(wait * 500))
                    html = pg.content()
                except Exception:                    # noqa: BLE001
                    pass
            if cjk(html) < min_chars:
                raise RuntimeError(f"real chrome got {len(html)} bytes but only "
                                   f"{cjk(html)} 汉字")
        # 首包可能是挑战页（412），跑完 JS 拿到的是真正文 → 这一趟算成功
        return 200, html.encode("utf-8"), "text/html; charset=utf-8"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:                            # noqa: BLE001
            pass
        subprocess.run(["rm", "-rf", profile], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def real_chrome_fetch(url: str, *, pause: float = 1.0, **kw) -> tuple[int, bytes, str, str, Path]:
    """抓取并落盘，返回 (status, content, content_type, sha256, path)。"""
    status, content, ctype = fetch_with_real_chrome(url, **kw)
    h = hashlib.sha256(content).hexdigest()
    d = RAW / h[:12]
    d.mkdir(parents=True, exist_ok=True)
    p = d / (url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index.html")
    p.write_bytes(content)
    time.sleep(pause)
    return status, content, ctype, h, p
