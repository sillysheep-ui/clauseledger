"""全国地级行政区门户可达性扫描。

思路：不自造城市清单（用民政部区划），不猜"我想到的"城市——
用 333 个地级单位，全名拼音 + 首字母缩写两个候选，逐个验证标题里是否真含该地名。
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pypinyin import lazy_pinyin

UA = "ClauseLedger-M0/0.1 (national reachability scan; contact: liuduo_sky@163.com)"
EXCLUDE = {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划", "市"}
_local = threading.local()


def client() -> httpx.Client:
    if not hasattr(_local, "c"):
        _local.c = httpx.Client(headers={"User-Agent": UA}, timeout=10, follow_redirects=True)
    return _local.c


def core_name(name: str) -> str:
    for suf in ("自治州", "地区", "盟", "市"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


# 多音字/译名/缩写与拼音对不上的，人工兜一次（一次性成本，不是长期维护）
OVERRIDE = {
    "长治市": "changzhi", "哈尔滨市": "harbin", "六安市": "luan", "石家庄市": "sjz",
    "乌鲁木齐市": "wlmq", "齐齐哈尔市": "qqhr", "攀枝花市": "pzh", "六盘水市": "lps",
    "巴音郭楞蒙古自治州": "bygl", "博尔塔拉蒙古自治州": "betl", "克孜勒苏柯尔克孜自治州": "kzls",
    "伊犁哈萨克自治州": "yl", "黔东南苗族侗族自治州": "qdn", "黔南布依族苗族自治州": "qn",
    "黔西南布依族苗族自治州": "qxn", "西双版纳傣族自治州": "xsbn", "德宏傣族景颇族自治州": "dh",
    "湘西土家族苗族自治州": "xx", "恩施土家族苗族自治州": "es", "延边朝鲜族自治州": "yb",
    "呼和浩特市": "hhht", "佳木斯市": "jms", "牡丹江市": "mdj", "张家界市": "zjj",
}


def candidates(name: str) -> list[str]:
    core = core_name(name)
    syl = lazy_pinyin(core)
    full = "".join(syl)
    abbr = "".join(s[0] for s in syl)
    out = [OVERRIDE[name]] if name in OVERRIDE else []
    for c in (full, abbr):
        if c not in out:
            out.append(c)
    return out


def visible(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>|\s+", "", t)


def decode(r: httpx.Response) -> str:
    """gov 站编码混乱：先 utf-8，出现替换字符再退 gb18030。"""
    t = r.content.decode("utf-8", "ignore")
    if "\ufffd" in t or t.count("\ufffd") > 0:
        t2 = r.content.decode("gb18030", "ignore")
        if len(t2) > len(t):
            return t2
    return t


def probe_one(item: dict) -> dict:
    name = item["name"]
    marker = core_name(name)[:2]
    rec = {"code": item["code"], "name": name, "province": item["provinceCode"],
           "tried": [], "ok_url": None, "chars": 0, "status": "unreachable"}
    for c in candidates(name):
      for url in (f"https://www.{c}.gov.cn/", f"http://www.{c}.gov.cn/", f"https://{c}.gov.cn/"):
        try:
            r = client().get(url)
        except Exception as e:
            rec["tried"].append({"url": url, "err": type(e).__name__})
            continue
        body = decode(r)
        txt = visible(body)
        hit = marker in body[:20000] or marker in txt[:3000]
        rec["tried"].append({"url": str(r.url), "status": r.status_code, "chars": len(txt), "marker": hit})
        if r.status_code == 200 and len(txt) > 300 and hit:
            rec.update(ok_url=str(r.url), chars=len(txt), status="ok")
            break
        if r.status_code == 200 and rec["status"] == "unreachable":
            rec["status"] = "low" if len(txt) <= 300 else "mismatch"
      if rec["status"] == "ok":
          break
    return rec


def main() -> None:
    raw = json.loads(Path("data/scan/cities.json").read_text("utf-8"))
    cities = [x for x in raw if x["name"] not in EXCLUDE]
    print(f"待扫 {len(cities)} 个地级单位", flush=True)
    out: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, rec in enumerate(pool.map(probe_one, cities), 1):
            out.append(rec)
            if i % 25 == 0:
                print(f"  {i}/{len(cities)}  已 ok={sum(1 for x in out if x['status']=='ok')}  {time.time()-t0:.0f}s", flush=True)
    Path("data/scan/national_portals.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    ok = [x for x in out if x["status"] == "ok"]
    print(f"\n完成 {len(out)} 个，耗时 {time.time()-t0:.0f}s")
    print(f"可达 {len(ok)} ({len(ok)/len(out):.0%})  low {sum(1 for x in out if x['status']=='low')}  "
          f"错配 {sum(1 for x in out if x['status']=='mismatch')}  不通 {sum(1 for x in out if x['status']=='unreachable')}")
    by_prov: dict[str, list[int]] = {}
    for x in out:
        by_prov.setdefault(x["province"], []).append(1 if x["status"] == "ok" else 0)
    worst = sorted(by_prov.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[:6]
    print("最差省份(省码 可达/总数):", [(p, f"{sum(v)}/{len(v)}") for p, v in worst])


if __name__ == "__main__":
    main()
