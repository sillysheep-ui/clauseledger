"""第二跳：省级门户用浏览器渲染后再找地市链接。

第一版用 httpx 抓省门户，吉林/江苏/湖南/甘肃/青海 一个链接都没找到——
不是没有导航，是导航由 JS 渲染或被 WAF 挡了。渲染后重找。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from tools.scan.province_discovery import PROV, core

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


async def links_of(ctx, url: str) -> list[tuple[str, str]]:
    page = await ctx.new_page()
    out: list[tuple[str, str]] = []
    try:
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        for a in await page.query_selector_all("a"):
            try:
                t = (await a.inner_text() or "").strip()
                h = await a.get_attribute("href") or ""
            except Exception:
                continue
            if t and h:
                out.append((t, page.url.rstrip("/") + "/" + h if h.startswith("/") else h))
    except Exception:
        pass
    finally:
        await page.close()
    return out


def probe(url: str, marker: str) -> int | None:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=12, follow_redirects=True)
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>|\s+", "", txt)
        return len(txt) if r.status_code == 200 and len(txt) > 300 and marker in txt else None
    except Exception:
        return None


async def main() -> None:
    data = json.loads(Path("data/scan/national_portals.json").read_text("utf-8"))
    cities = json.loads(Path("data/scan/cities.json").read_text("utf-8"))
    pcode = {c["name"]: str(c["provinceCode"]) for c in cities}
    todo = [x for x in data if x["status"] != "ok"]
    by_prov: dict[str, list[dict]] = {}
    for x in todo:
        by_prov.setdefault(pcode.get(x["name"], ""), []).append(x)
    print(f"待补 {len(todo)} 个城市，跨 {len(by_prov)} 个省", flush=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="zh-CN", user_agent=UA)
        for prov, group in by_prov.items():
            dom = PROV.get(prov)
            if not dom:
                continue
            base = f"https://www.{dom}.gov.cn/"
            links = await links_of(ctx, base)
            if not links:
                print(f"  {prov} 门户无链接", flush=True)
                continue
            fixed = 0
            for x in group:
                c = core(x["name"])
                cands = [h for t, h in links if c[:2] in t]
                for u in cands[:3]:
                    n = probe(u, c[:2])
                    if n:
                        x.update(status="ok", ok_url=u, chars=n, via="province2")
                        fixed += 1
                        break
            print(f"  {prov}: {fixed}/{len(group)}", flush=True)
        await b.close()

    Path("data/scan/national_portals.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    ok = sum(1 for x in data if x["status"] == "ok")
    print(f"可达 {ok}/{len(data)} ({ok / len(data):.0%})")
    print("剩余：", [x["name"] for x in data if x["status"] != "ok"])


if __name__ == "__main__":
    asyncio.run(main())
