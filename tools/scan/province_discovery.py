"""省级门户 → 地市链接：用权威导航补齐拼音猜不到的城市。

拼音猜域名的天花板约 71%，剩下的大多是域名不合拼音（哈尔滨 harbin、长治 changzhi）。
省级门户的"市州导航"是权威入口，比再猜一次靠谱。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

UA = "ClauseLedger-M0/0.1 (province discovery; contact: liuduo_sky@163.com)"
EXCLUDE = {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划", "市"}

PROV = {
    "11": "beijing", "12": "tj", "13": "hebei", "14": "shanxi", "15": "nmg",
    "21": "ln", "22": "jl", "23": "hlj", "31": "shanghai", "32": "jiangsu",
    "33": "zj", "34": "ah", "35": "fj", "36": "jiangxi", "37": "shandong",
    "41": "henan", "42": "hubei", "43": "hunan", "44": "gd", "45": "gxzf",
    "46": "hainan", "50": "cq", "51": "sc", "52": "guizhou", "53": "yn",
    "54": "xizang", "61": "shaanxi", "62": "gansu", "63": "qinghai",
    "64": "nx", "65": "xinjiang",
}


def core(name: str) -> str:
    for suf in ("自治州", "地区", "盟", "市"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def visible(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>|\s+", "", t)


def decode(r: httpx.Response) -> str:
    t = r.content.decode("utf-8", "ignore")
    return t if t.count("\ufffd") == 0 else r.content.decode("gb18030", "ignore")


def province_links(code: str, cities: list[str]) -> dict[str, str]:
    """返回 {城市名: 候选URL}，只取同域、锚文本含城市名的链接。"""
    dom = PROV.get(code)
    if not dom:
        return {}
    url = f"https://www.{dom}.gov.cn/"
    got: dict[str, str] = {}
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        body = decode(r)
        soup = BeautifulSoup(body, "html.parser")
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            if not txt or len(txt) > 20:
                continue
            for c in cities:
                k = core(c)
                if (k in txt or k[:2] in txt) and c not in got:
                    href = a["href"]
                    if href.startswith(("javascript:", "#")):
                        continue
                    got[c] = str(httpx.URL(str(r.url)).join(href))
        if len(got) < len(cities) // 2:      # 首页不够就再跳一层「市州导航」
            for a in soup.find_all("a", href=True):
                if a.get_text(strip=True) in {"市州", "地市", "城市", "市州政府", "地方", "市县"}:
                    try:
                        r2 = httpx.get(str(httpx.URL(str(r.url)).join(a["href"])),
                                       headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
                        for b in BeautifulSoup(decode(r2), "html.parser").find_all("a", href=True):
                            t2 = b.get_text(" ", strip=True)
                            for c in cities:
                                if (core(c)[:2] in t2) and c not in got and not b["href"].startswith(("javascript:", "#")):
                                    got[c] = str(httpx.URL(str(r2.url)).join(b["href"]))
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    return got


def probe(url: str, name: str) -> dict | None:
    marker = core(name)[:2]
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=12, follow_redirects=True)
        body = decode(r)
        txt = visible(body)
        if r.status_code == 200 and len(txt) > 300 and marker in body[:20000]:
            return {"ok_url": str(r.url), "chars": len(txt), "status": "ok"}
        return None
    except Exception:
        return None


def main() -> None:
    data = json.loads(Path("data/scan/national_portals.json").read_text("utf-8"))
    cities_all = json.loads(Path("data/scan/cities.json").read_text("utf-8"))
    by_prov: dict[str, list[str]] = {}
    for c in cities_all:
        if c["name"] not in EXCLUDE:
            by_prov.setdefault(c["provinceCode"], []).append(c["name"])

    missing = [x for x in data if x["status"] != "ok"]
    print(f"待补 {len(missing)} 个城市所在的省：{sorted({x['province'] for x in missing})}", flush=True)

    found: dict[str, str] = {}
    for code in sorted({x["province"] for x in missing}):
        links = province_links(code, by_prov.get(code, []))
        need = [x["name"] for x in missing if x["province"] == code]
        hit = {k: v for k, v in links.items() if k in need}
        found.update(hit)
        print(f"  省 {code}: 导航命中 {len(hit)}/{len(need)}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(found.keys(), pool.map(lambda kv: probe(kv[1], kv[0]), found.items())))

    fixed = 0
    for x in data:
        if x["name"] in results and results[x["name"]]:
            x.update(results[x["name"]])
            x["tried"].append({"url": x["ok_url"], "via": "province_discovery"})
            fixed += 1
    Path("data/scan/national_portals.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    ok = sum(1 for x in data if x["status"] == "ok")
    print(f"\n省级发现修复 {fixed} 个；现在可达 {ok}/{len(data)} ({ok/len(data):.0%})")


if __name__ == "__main__":
    main()
