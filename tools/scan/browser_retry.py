"""用真实浏览器重试被拦/空壳的城市门户。

三类拦截，处理方式相同但原因不同：
- 空壳 SPA：渲染后取文本即可
- 加速乐 / CT2-WAAP / 瑞数：首次响应是挑战页（521/412/405），
  JS 执行后写 cookie，**必须再请求一次**才拿得到正文。
  所以每个 URL 试两次，判据是正文而不是状态码。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"


def core(name: str) -> str:
    for suf in ("自治州", "地区", "盟", "市"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


async def one(ctx, rec: dict, sem: asyncio.Semaphore) -> None:
    marker = core(rec["name"])[:2]
    urls = [t["url"] for t in rec["tried"] if isinstance(t.get("url"), str)][:2]
    urls = [u for u in urls if u.startswith("http")]
    async with sem:
        for u in urls:
            for attempt in (1, 2):
                page = await ctx.new_page()
                try:
                    r = await page.goto(u, timeout=20000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    body = await page.inner_text("body")
                    if len(body) > 500 and marker in (await page.title()) + body[:4000]:
                        rec.update(status="ok", ok_url=page.url, chars=len(body),
                                   via="browser", http_status=int(r.status) if r else None)
                        rec.pop("waf", None)
                        return
                    if attempt == 2:
                        rec["waf"] = {"status": int(r.status) if r else None, "chars": len(body),
                                      "cookies": [c["name"] for c in await ctx.cookies()][:4],
                                      "title": (await page.title())[:24]}
                except Exception as e:
                    if attempt == 2:
                        rec["waf"] = {"err": type(e).__name__}
                finally:
                    await page.close()


async def main() -> None:
    data = json.loads(Path("data/scan/national_portals.json").read_text("utf-8"))
    todo = [x for x in data if x["status"] != "ok"]
    print(f"浏览器重试 {len(todo)} 个", flush=True)
    sem = asyncio.Semaphore(3)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
                                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(locale="zh-CN", viewport={"width": 1440, "height": 900}, user_agent=UA)
        await ctx.add_init_script(STEALTH)
        await asyncio.gather(*(one(ctx, x, sem) for x in todo))
        await b.close()
    Path("data/scan/national_portals.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    ok = sum(1 for x in data if x["status"] == "ok")
    fixed = [x["name"] for x in todo if x["status"] == "ok"]
    print(f"修复 {len(fixed)} 个：{fixed}")
    print(f"现在可达 {ok}/{len(data)} ({ok / len(data):.0%})")
    left = [x["name"] for x in data if x["status"] != "ok"]
    print(f"剩余 {len(left)}：{left}")


if __name__ == "__main__":
    asyncio.run(main())
