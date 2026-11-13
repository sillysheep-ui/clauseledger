"""把"真 Chrome 重测"的结果写回 coverage.json。

为什么要有这一步：`blocked` 是**用 httpx 探出来的结论**，不等于"这个站不可达"。
实测 24 个 blocked 里，21 个是域名 DNS 根本解析不了（我们猜错了域名），
1 个是城市名碰撞（taizhou.gov.cn 是江苏泰州，浙江台州在 zjtz.gov.cn），
2 个是网络超时。换正确域名 + 真 Chrome 重测后，24 个里 14 个能打开。

规矩：只把**验证过、且正文里出现城市名**的改成 reachable，其余保持 blocked，
并且把"为什么 blocked"从笼统的 waf_blocked 改成具体原因。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

COV = Path("data/coverage.json")
RETRY = Path("data/scan/chrome_retry2.json")
TZ = timezone(timedelta(hours=8))


def main() -> int:
    cov = json.loads(COV.read_text())
    rows = {r["city"]: r for r in json.loads(RETRY.read_text())}
    now = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    flipped = 0
    for city, r in rows.items():
        rec = cov["cities"].get(city)
        if not rec or r.get("result") != "reachable":
            continue
        rec.update({
            "status": "reachable",
            "method": "chrome",
            "last_status": 200,
            "url": r["url"],
            "chars": r["cjk"],
            "checked_at": now,
        })
        for k in ("reason", "detail", "caveat"):
            rec.pop(k, None)
        flipped += 1

    reach = sum(1 for v in cov["cities"].values() if v.get("status") == "reachable")
    blocked = [v for v in cov["cities"].values() if v.get("status") == "blocked"]
    by = Counter(v.get("reason", "unknown") for v in blocked)
    cov["totals"] = {
        "cities_total": len(cov["cities"]),
        "cities_reachable": reach,
        "cities_blocked": len(blocked),
        "blocked_by_reason": dict(by),
    }
    cov["generated_at"] = now
    COV.write_text(json.dumps(cov, ensure_ascii=False, indent=1))
    print(f"翻正 {flipped} 个城市 → 可达 {reach}/{len(cov['cities'])}，仍 blocked {len(blocked)}")
    print("blocked 原因分布:", dict(by))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
