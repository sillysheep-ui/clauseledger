"""用真 Chrome 重测 blocked 城市：把"直连拿不到"和"真的没有"分开。

背景：24 个 blocked 全部是 `method=httpx` 探出来的——也就是"用 httpx 直连拿不到"，
不等于"这个站不开放"。实测成都在 httpx 下 412/0 字，在真 Chrome 下 5,030 汉字。
所以这 24 个必须换客户端重测一遍，结论才有资格叫"不可达"。

判据不是状态码：正文汉字 >= 200 且出现城市名，才算可达。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.ingest.cdp import real_chrome_fetch

COV = Path("data/coverage.json")
PORTALS = Path("data/scan/national_portals.json")
OUT = Path("data/scan/chrome_retry.json")


def cjk(t: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa5]", t))


def candidates(name: str, portals: list[dict]) -> list[str]:
    for p in portals:
        if p["name"].startswith(name):
            urls = [t["url"] for t in p.get("tried", []) if t.get("url")]
            urls += [u for u in [p.get("ok_url")] if u]
            seen, out = set(), []
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    out.append(u)
            return out[:3]
    return []


def main() -> int:
    cov = json.loads(COV.read_text())["cities"]
    portals = json.loads(PORTALS.read_text())
    blocked = [k for k, v in cov.items() if v.get("status") == "blocked"]
    print(f"blocked {len(blocked)} 个城市，用真 Chrome 重测\n")

    rows = []
    for name in blocked:
        urls = candidates(name, portals)
        row = {"city": name, "tried": []}
        if not urls:
            row["result"] = "no_url"
            print(f"{name:4s} 没有候选 URL（当初就没找到入口）")
            rows.append(row)
            continue
        best = None
        for u in urls:
            try:
                st, content, _ = real_chrome_fetch(u, wait=8.0)
                text = content.decode("utf-8", "ignore")
                n = cjk(text)
                hit = n >= 200 and name[:2] in text
                row["tried"].append({"url": u, "status": st, "cjk": n, "marker": hit})
                if hit:
                    best = (u, n)
                    break
            except Exception as e:  # noqa: BLE001
                row["tried"].append({"url": u, "err": str(e)[:120]})
        if best:
            row["result"], row["ok_url"], row["cjk"] = "reachable", best[0], best[1]
            print(f"{name:4s} 可达 {best[1]} 字 ← {best[0]}")
        else:
            row["result"] = "still_blocked"
            print(f"{name:4s} 仍不可达（试了 {len(urls)} 个入口）")
        rows.append(row)

    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    ok = sum(1 for r in rows if r["result"] == "reachable")
    no = sum(1 for r in rows if r["result"] == "no_url")
    print(f"\n可达 {ok} / 仍不可达 {len(rows) - ok - no} / 当初就没入口 {no}")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
