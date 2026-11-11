"""用真实浏览器重试被拦/空壳的城市门户。

httpx 拿到 403/412 或 200 但只有几百字节时，用 Chromium 再试一次：
SPA 壳与部分 UA 拦截能解，412 挑战类通常仍解不了（成都、合肥）。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
EXCLUDE = {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划", "市"}


def core(name: str) -> str:
    for suf in ("自治州", "地区", "盟", "市"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


async def try_city(ctx, rec: dict, sem: asyncio.Semaphore) -> None:
    marker = core(rec["name"])[:2]
    urls = [t["url"] for t in rec["tried"] if isinstance(t.get("url"), str)][:3]
    urls = [u for u in urls if u.startswith("http")]
    async with sem:
        for u in urls:
            page = await ctx.new_page()
            try:
                r = await page.goto(u, timeout=18000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1800)
                body = await page.inner_text("body")
                if r and r.status == 200 and len(body) > 300 and marker in (await page.title() + body[:5000]):
                    rec.update(status="ok", ok_url=page.url, chars=len(body), via="browser")
                    return
            except Exception:
                pass
            finally:
                await page.close()


async def main() -> None:
    data = json.loads(Path("data/scan/national_portals.json").read_text("utf-8"))
    todo = [x for x in data if x["status"] != "ok"]
    print(f"浏览器重试 {len(todo)} 个", flush=True)
    sem = asyncio.Semaphore(3)
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        ctx = await b.new_context(locale="zh-CN", viewport={"width": 1440, "height": 900}, user_agent=UA)
        await asyncio.gather(*(try_city(ctx, x, sem) for x in todo))
        await b.close()
    Path("data/scan/national_portals.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    ok = sum(1 for x in data if x["status"] == "ok")
    fixed = [x["name"] for x in todo if x["status"] == "ok"]
    print(f"浏览器修复 {len(fixed)} 个：{fixed}")
    print(f"现在可达 {ok}/{len(data)} ({ok/len(data):.0%})")


if __name__ == "__main__":
    asyncio.run(main())
