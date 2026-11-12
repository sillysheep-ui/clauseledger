"""落户评分：把「规则」和「人」对上，产出四态结论与分数区间。

为什么不是检索：

    72 分不是一段文字，是一张表。检索能告诉你"评分办法在第 21–26 页"，
    但回答"我 63 分够不够"需要逐项求和——这是计算，不是找句子。

为什么不用 LLM 判：

    每一项的分值是确定的、互斥的、有上限的。LLM 在这里唯一的作用是把人
    的自述映射到字段（"我六级 520" → foreign=cet6_or_tem8），那一步单独做，
    且必须回显原文让人确认。判定本身交给算术。

三个关键约定：

    1. **unknown 不是 0 分**。缺信息时给区间 [已知分, 上限]，不说"你不够"。
    2. **不满足只在"上限都够不到"时才说**。否则是误导。
    3. 每一项都带页码，能回原文核对。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RULE_PATH = Path(__file__).resolve().parents[2] / "rules" / "sh" / "luohu-2026.yaml"

# 互斥组：同一组里最多一项得分（"同类奖励取最高"）
EXCLUSIVE = {
    "degree": ("degree.master", "degree.bachelor"),
    "school": ("school.first", "school.second", "school.other"),
    "gpa": ("gpa.a", "gpa.b", "gpa.c", "gpa.d"),
    "foreign": ("foreign.cet6", "foreign.cet4", "foreign.major_pass"),
    "computer": ("computer.grad", "computer.sci_high", "computer.arts_mid",
                 "computer.sci_mid", "computer.arts_low", "computer.art_sport"),
    "honor": ("honor.national", "honor.province", "honor.school"),
    "competition": ("competition.national_first", "competition.national_second",
                    "competition.national_third", "competition.local_first",
                    "competition.local_second", "competition.local_third"),
}

# 明文写的不累计：上海市中小学、幼儿园 与 远郊地区教育卫生农业等公益事业单位
MUTEX = (("employer.school", "employer.remote_public"),)


@dataclass
class ItemResult:
    id: str
    label: str
    state: str          # satisfied / unsatisfied / unknown / not_applicable
    points: int          # 实际得分（satisfied 才有）
    cap_points: int      # 该项满分
    page: int
    reason: str


@dataclass
class Verdict:
    verdict: str                 # satisfied / unsatisfied / unknown / direct
    total_known: int             # 确定能拿到的分
    total_upper: int             # 把所有 unknown 都当成得分后的上限
    standard: int
    gap: int                     # standard - total_known
    items: list[ItemResult] = field(default_factory=list)
    direct: str | None = None
    notes: list[str] = field(default_factory=list)


def load_rules(path: Path | str = RULE_PATH) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text("utf-8"))


def _match(when: dict, profile: dict) -> str:
    """返回 satisfied / unsatisfied / unknown —— 三态，不含 not_applicable。"""
    for k, v in when.items():
        if k not in profile or profile[k] is None:
            return "unknown"
        if profile[k] != v:
            return "unsatisfied"
    return "satisfied"


def evaluate(profile: dict, rules: dict | None = None) -> Verdict:
    rules = rules or load_rules()
    standard = int(rules["meta"]["standard_score"])

    # 直落条件优先，不进入打分
    for d in rules.get("direct_rules", []):
        if _match(d["when"], profile) == "satisfied":
            return Verdict("direct", 0, 0, standard, 0, direct=d["label"],
                           notes=["符合直落条件，不适用评分办法"])
    for s in rules.get("special", []):
        if _match(s["when"], profile) == "satisfied":
            return Verdict("direct", 0, 0, standard, 0, direct=s["label"],
                           notes=[f"特殊条件：{s['label']}（第 {s['page']} 页）"])

    raw: dict[str, dict] = {}
    for g in rules["groups"]:
        for it in g["items"]:
            raw[it["id"]] = it

    results: dict[str, ItemResult] = {}
    for iid, it in raw.items():
        st = _match(it["when"], profile)
        results[iid] = ItemResult(iid, it["label"], st, 0, int(it["points"]), int(it["page"]),
                                  "个人信息未提供" if st == "unknown" else
                                  ("符合" if st == "satisfied" else "不符合"))

    # 互斥组：只保留分最高的一项，其余 satisfied → not_applicable
    for name, ids in EXCLUSIVE.items():
        present = [results[i] for i in ids if i in results and results[i].state == "satisfied"]
        if len(present) > 1:
            present.sort(key=lambda r: r.cap_points, reverse=True)
            for r in present[1:]:
                r.state = "not_applicable"
                r.reason = f"同组「{name}」取最高分，本项不累计"

    for a, b in MUTEX:
        if a in results and b in results:
            if results[a].state == "satisfied" and results[b].state == "satisfied":
                keep, drop = (results[a], results[b]) if results[a].cap_points >= results[b].cap_points else (results[b], results[a])
                drop.state = "not_applicable"
                drop.reason = "两项明文不累计，取较高者"

    group_of = {iid: name for name, ids in EXCLUSIVE.items() for iid in ids}

    def contrib(ids: list[str]) -> tuple[int, int]:
        """一组条目的（确定得分, 上限）。同组互斥 → 上限取组内最大值。"""
        k = u = 0
        seen: set[str] = set()
        for iid in ids:
            g = group_of.get(iid)
            if g and g in seen:
                continue
            r = results.get(iid)
            if r is None:
                continue
            if g:
                seen.add(g)
                mates = [results[i] for i in EXCLUSIVE[g] if i in results]
                sat = [m for m in mates if m.state == "satisfied"]
                unk = [m.cap_points for m in mates if m.state == "unknown"]
                k += sum(m.cap_points for m in sat)
                u += max([m.cap_points for m in sat] + unk) if (sat or unk) else 0
            elif r.state == "satisfied":
                k += r.cap_points
                u += r.cap_points
            elif r.state == "unknown":
                u += r.cap_points
        return k, u

    all_ids = list(results)
    known, upper = contrib(all_ids)

    notes: list[str] = []
    for cap in rules.get("caps", []):
        ids = [i for i in cap["items"] if i in results]
        k, u = contrib(ids)
        if k > cap["max"] or u > cap["max"]:
            known -= k - min(k, cap["max"])
            upper -= u - min(u, cap["max"])
            notes.append(f"「{cap['note']}」（第 {cap['page']} 页）")

    for r in results.values():
        if r.state == "satisfied":
            r.points = r.cap_points

    if known >= standard:
        verdict = "satisfied"
    elif upper < standard:
        verdict = "unsatisfied"
    else:
        verdict = "unknown"
        notes.append(f"还有 {upper - known} 分取决于未提供的信息，够不够目前判不了")

    return Verdict(verdict, known, upper, standard, standard - known,
                   items=list(results.values()), notes=notes)
