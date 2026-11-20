"""核验模型写的答案：引用能不能落地、数字有没有出处。

这是"生成"和"可信"之间唯一的一道机械检查。它抓不到语义错（把"可以"读成"应当"），
但能抓到三类最常见的硬错：编造编号、引用不存在的证据、写出证据里没有的数字。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .context import Evidence

NUM = re.compile(r"\d+(?:\.\d+)?")
EID = re.compile(r"\[?(E\d+)\]?")


@dataclass
class Check:
    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    numbers_checked: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.kept) and not any(i.startswith("数字") for i in self.issues)

    def to_dict(self) -> dict:
        return {"kept": len(self.kept), "dropped": len(self.dropped),
                "issues": self.issues, "numbers_checked": self.numbers_checked,
                "ok": self.ok}


def _cited_text(cites: list[str], by_id: dict[str, Evidence]) -> str:
    return "\n".join(by_id[c].text for c in cites if c in by_id)


def check(draft, ev: list[Evidence], *, rules_text: str = "") -> Check:
    by_id = {e.eid: e for e in ev}
    res = Check()
    for s in draft.sentences:
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        cites = [str(c).strip("[]") for c in (s.get("cites") or [])]
        cites = [c for c in cites if c]
        if not cites:
            res.dropped.append({**s, "why": "没有引用"})
            res.issues.append("引用：有一句没有标注证据编号")
            continue
        bad = [c for c in cites if c not in by_id]
        if bad:
            res.dropped.append({**s, "why": f"编号不存在 {bad}"})
            res.issues.append(f"引用：出现了不存在的编号 {bad}")
            continue

        # 数字必须能在被引证据（或规则结果）里找到
        src = _cited_text(cites, by_id) + "\n" + (rules_text or "")
        missing = [n for n in NUM.findall(text) if n not in src]
        if missing:
            res.dropped.append({**s, "why": f"数字无出处 {missing}"})
            res.issues.append(f"数字：{missing} 不在被引证据里")
            continue

        res.kept.append({**s, "cites": cites})
        res.numbers_checked += len(NUM.findall(text))
    return res


def merge(draft, chk: Check):
    """把核验后的句子写回 draft（丢弃的句子不再出现在答案里）。"""
    draft.sentences = chk.kept
    return draft
