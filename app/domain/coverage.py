"""覆盖声明：在检索之前就告诉用户哪里没有。

设计要点：**城市可达 ≠ 事项已建库**，两者必须分开说。
否则"广州没查到落户政策"会被读成"广州没有落户政策"，这是两种完全不同的错。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CoverageStatus = Literal["covered", "partial", "blocked", "out_of_scope"]

# 自治州全名去掉民族名才像人话：湘西土家族苗族自治州 -> 湘西
ETHNIC = ("土家|蒙古|哈萨克|柯尔克孜|维吾尔|塔吉克|塔塔尔|乌孜别克|俄罗斯|朝鲜|藏|回|苗|傣|白|彝|壮|侗"
          "|瑶|畲|满|黎|佤|羌|怒|独龙|傈僳|拉祜|纳西|景颇|布朗|阿昌|普米|德昂|基诺|门巴|珞巴|锡伯"
          "|达斡尔|鄂温克|鄂伦春|赫哲|保安|撒拉|东乡|裕固|仡佬|仫佬|毛南|水|京|高山")


def normalize_city(name: str) -> str:
    key = name.strip()
    for suf in ("自治州", "地区", "盟", "市"):
        if key.endswith(suf):
            key = key[: -len(suf)]
            break
    return re.split(ETHNIC, key)[0] or key


@dataclass(frozen=True)
class Coverage:
    status: CoverageStatus
    city: str
    matter: str | None
    message: str
    caveat: str | None = None

    @property
    def allows_retrieval(self) -> bool:
        """blocked / out_of_scope 不允许起检索——跑了也只是白跑，还会让用户以为查过了。"""
        return self.status in ("covered", "partial")


class CoverageIndex:
    def __init__(self, doc: dict) -> None:
        self.doc = doc
        self.cities: dict[str, dict] = doc.get("cities", {})
        self.totals = doc.get("totals", {})

    @classmethod
    def load(cls, path: str | Path = "data/coverage.json") -> "CoverageIndex":
        return cls(json.loads(Path(path).read_text("utf-8")))

    def check(self, city: str, matter: str | None = None) -> Coverage:
        key = normalize_city(city)
        rec = self.cities.get(key) or self.cities.get(city)
        if rec is None:
            return Coverage("out_of_scope", city, matter,
                            f"{city}不在收录范围（目前只覆盖地级行政区，共 {self.totals.get('cities_total', 0)} 个）")
        if rec["status"] == "blocked":
            return Coverage("blocked", city, matter,
                            f"{city}未接入：{rec.get('detail', '站点无法自动获取')}",
                            rec.get("caveat"))
        matters = rec.get("matters") or {}
        if matter and matter not in matters:
            return Coverage("partial", city, matter,
                            f"{city}站点已接入，但「{matter}」尚未建库",
                            "站点可达不代表该事项已有可引用的条款")
        return Coverage("covered", city, matter, f"{city}已接入")

    def disclosure(self) -> str:
        """首屏横幅：一句话说清覆盖与缺口，不藏在设置里。"""
        t = self.totals
        return (f"已接入 {t.get('cities_reachable', 0)}/{t.get('cities_total', 0)} 个城市"
                f"（语料版本 {self.doc.get('corpus_version') or '构建中'}），"
                f"{t.get('cities_blocked', 0)} 个未接入")

    def blocked_cities(self) -> list[str]:
        return [k for k, v in self.cities.items() if v["status"] == "blocked"]
