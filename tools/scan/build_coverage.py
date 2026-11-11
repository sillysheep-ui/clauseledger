"""把门户扫描结果编译成"覆盖声明"数据。

检索前要先告诉用户哪里不可达——这份文件就是那句话的数据来源。
不可达有四种原因，用户看到的解释必须不同（不能一律说"暂无数据"）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.domain.coverage import normalize_city

CST = timezone(timedelta(hours=8))
WAF = {400, 403, 405, 412, 521, 502, 503}


def reason_of(rec: dict) -> tuple[str, str]:
    tried = rec.get("tried") or []
    codes = {t.get("status") for t in tried if isinstance(t.get("status"), int)}
    if codes & WAF:
        return "waf_blocked", "站点可访问但部署了挑战式防护（412/521），自动化拿不到正文"
    if 200 in codes:
        return "identity_mismatch", "域名能打开，但页面与城市对不上（多音字域名碰撞或防护页顶替）"
    # 全部连接失败 ≠ 当地没有官网，只说明发现链没走通（省门户导航被拦或未暴露）
    return "discovery_gap", "我们没找到该城市的官方入口（省级导航未取到），不是当地没有公开信息"


def main() -> None:
    src = json.loads(Path("data/scan/national_portals.json").read_text("utf-8"))
    cities = json.loads(Path("data/scan/cities.json").read_text("utf-8"))
    prov = {str(c["code"]): c["name"] for c in cities if str(c["code"]).endswith("0000")}
    now = datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S%z")
    out = {"schema": "coverage/v1", "generated_at": now, "corpus_version": None,
           "totals": {}, "cities": {}}
    blocked_by_reason: dict[str, int] = {}
    for r in src:
        key = normalize_city(r["name"])
        item = {"province": prov.get(r.get("province", ""), ""), "checked_at": now}
        if r["status"] == "ok":
            item.update(status="reachable", url=r.get("ok_url"), chars=r.get("chars"),
                        matters={}, departments={})
        else:
            why, detail = reason_of(r)
            blocked_by_reason[why] = blocked_by_reason.get(why, 0) + 1
            item.update(status="blocked", reason=why, detail=detail,
                        caveat="未接入不代表当地没有相关政策，只代表我们没能自动取到")
        out["cities"][key] = item
    # 直辖市不在"地级行政区"名单里，但是共同的目标城市，单独登记（2026-11-11 实测均 200）
    for name, url, chars in (("北京", "https://www.beijing.gov.cn/", 5285),
                             ("上海", "https://www.shanghai.gov.cn/", 3599),
                             ("天津", "https://www.tj.gov.cn/", 3346),
                             ("重庆", "https://www.cq.gov.cn/", 3505)):
        out["cities"][name] = {"province": name, "checked_at": now, "status": "reachable",
                               "url": url, "chars": chars, "matters": {}, "departments": {},
                               "kind": "municipality"}
    ok = sum(1 for c in out["cities"].values() if c["status"] == "reachable")
    out["totals"] = {"cities_total": len(out["cities"]), "cities_reachable": ok,
                     "cities_blocked": len(out["cities"]) - ok,
                     "blocked_by_reason": blocked_by_reason}
    Path("data/coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"{ok}/{len(out['cities'])} 可达；{blocked_by_reason}")
    print("blocked 例:", [k for k, v in out["cities"].items() if v["status"] == "blocked"][:8])


if __name__ == "__main__":
    main()
