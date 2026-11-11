"""城市站点矩阵：从门户首页发现部门站，测可抓性。

不猜域名 —— 猜会漏（上次福州、青岛就是这么漏的）。
做法：抓门户首页 → 按锚文本找部门入口 → 逐个测正文可达性。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

UA = "ClauseLedger-M0/0.1 (site matrix; contact: liuduo_sky@163.com)"

CITIES = {
    "上海": "https://www.shanghai.gov.cn",
    "北京": "https://www.beijing.gov.cn",
    "杭州": "https://www.hangzhou.gov.cn",
    "武汉": "https://www.wuhan.gov.cn",
    "广州": "https://www.gz.gov.cn",
    "西安": "https://www.xa.gov.cn",
    "长沙": "https://www.changsha.gov.cn",
    "济南": "https://www.jinan.gov.cn",
    "郑州": "https://www.zhengzhou.gov.cn",
    "昆明": "https://www.km.gov.cn",
    "贵阳": "https://www.gy.gov.cn",
    "苏州": "https://www.suzhou.gov.cn",
    "厦门": "https://www.xm.gov.cn",
    "宁波": "https://www.ningbo.gov.cn",
    "南昌": "https://www.nc.gov.cn",
}

# 站点类型 -> 锚文本关键词（按优先级）
DEPT_RULES: dict[str, list[str]] = {
    "人社": ["人力资源和社会保障", "人社局", "人力社保", "人社"],
    "教育": ["教育委员会", "教育局", "教委"],
    "公安": ["公安局"],
    "住建": ["住房和城乡建设", "住建", "住房保障和房屋", "房管局"],
    "医保": ["医疗保障", "医保"],
    "公积金": ["公积金"],
}


def visible_text(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>|\s+", "", t)


def probe(cli: httpx.Client, url: str) -> dict:
    try:
        r = cli.get(url)
        chars = len(visible_text(r.text)) if "html" in r.headers.get("content-type", "") else 0
        return {"url": str(r.url), "status": r.status_code, "chars": chars,
                "ok": r.status_code == 200 and chars > 800}
    except Exception as e:
        return {"url": url, "status": None, "chars": 0, "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:40]}"}


NAV_TEXTS = ["政府部门", "市直部门", "政府机构职能目录", "政府机构", "机构职能", "组织机构", "部门"]


def find_nav_pages(html: str, base: str) -> list[str]:
    """第二跳：很多门户首页只给「部门」导航页入口，部门站在下一层。"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if txt in NAV_TEXTS:
            href = a["href"]
            if not href.startswith(("javascript:", "#")):
                out.append(str(httpx.URL(base).join(href)))
    return out[:3]


def find_links(html: str, base: str, keywords: list[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if any(k in txt for k in keywords) and len(txt) <= 30:
            href = a["href"]
            if href.startswith(("javascript:", "#", "mailto:")):
                continue
            out.append(httpx.URL(base).join(href).__str__())
    # 同域去重保序
    seen, uniq = set(), []
    for u in out:
        key = re.sub(r"^https?://(www\.)?", "", u).split("?")[0]
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    return uniq[:4]


def main() -> None:
    rows = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=12, follow_redirects=True) as cli:
        for city, portal in CITIES.items():
            row = {"city": city, "portal": probe(cli, portal), "depts": {}}
            ok = row["portal"]["ok"]
            if ok:
                resp = cli.get(portal)
                html = resp.text
                pool = html  # 两跳：门户 + 部门导航页
                for nav in find_nav_pages(html, str(resp.url)):
                    try:
                        nr = cli.get(nav)
                        if nr.status_code == 200:
                            pool += nr.text
                            row.setdefault("nav_pages", []).append(nav)
                    except Exception:
                        pass
                    time.sleep(0.4)
                for dept, kws in DEPT_RULES.items():
                    cands = find_links(pool, str(resp.url), kws)
                    best = None
                    for u in cands:
                        p = probe(cli, u)
                        time.sleep(0.5)
                        if p["ok"]:
                            best = p
                            break
                        best = best or p
                    row["depts"][dept] = best
            rows.append(row)
            marks = "".join("✓" if (row["depts"].get(d) or {}).get("ok") else "·" for d in DEPT_RULES)
            print(f"{city:<4}{'✓' if ok else '✗'} 门户{row['portal']['chars']:>5}字  {marks}", flush=True)
            Path("data/scan").mkdir(parents=True, exist_ok=True)
            Path("data/scan/city_matrix.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(0.6)


if __name__ == "__main__":
    main()
