"""切分体检：不看检索，只看"条款切得对不对"。

为什么要单独体检：实验 B 已经证明——三版解析器（v0.3.1 / v0.3.2 / v0.3.2+）
跑同一批题，hit@1 全 54%、top-1 命中文档 0 条变化。检索指标对这种改动是瞎的。
所以切分质量必须用**结构不变量**来量，而不是用命中率。

七条不变量（全部机械可判）：

  I0 偏移一致   条款文本 == 全文[start:end]（文本与区间漂移会立刻暴露）
  I1 标签自洽   条款文本必须以自己的编号开头（切在句中会立刻违反）
  I2 父含子     父条款文本必须包含子条款文本（区间没算对会漏字）
  I3 区间有序   子条款 start 递增，且落在父区间内
  I4 兄弟不重叠 同级兄弟区间不应相交
  I5 覆盖完整   叶子条款区间并集应盖住全文（空洞 = 正文从未进索引）
  I6 无碎片段   条款文本 < 15 字视为碎片
  I7 无粘连段   单条 > 3000 字视为没切开

分层：按载体（html / pdf 文本层 / OCR / docx / doc）分别统计，
因为"不同格式处理得对不对"正是要回答的问题。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path("data/processed")
MIN_CHARS = 15
MAX_CHARS = 3000


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


@dataclass
class Report:
    doc: str
    carrier: str
    clauses: int
    chars: int
    blocks: int
    i0_bad: list[str] = field(default_factory=list)
    i1_bad: list[str] = field(default_factory=list)
    i2_bad: list[str] = field(default_factory=list)
    i3_bad: list[str] = field(default_factory=list)
    i4_bad: list[str] = field(default_factory=list)
    i6_bad: int = 0
    i7_bad: int = 0
    i5_gap: float = 0.0
    dup_path: int = 0
    dup_kinds: int = 0

    @property
    def density(self) -> float:
        return self.clauses / (self.chars / 1000) if self.chars else 0.0


def carrier(rec: dict) -> str:
    ext = rec.get("extract") or "native"
    if rec.get("page_kind") == "pdf":
        return "pdf-native" if ext == "native" else ext
    return {"ocr": "pdf-ocr", "textutil": "doc", "native": "html"}.get(ext, ext)


def check(rec: dict) -> Report:
    blocks = rec.get("blocks") or []
    clauses = rec.get("clauses") or []
    lines = [ln for b in blocks for ln in (b.get("text") or "").split("\n") if ln.strip()]
    total = sum(len(ln) + 1 for ln in lines)
    r = Report(doc=rec.get("doc_no") or rec.get("title", "?"),
               carrier=carrier(rec), clauses=len(clauses),
               chars=sum(len(ln) for ln in lines), blocks=len(blocks))

    full = "\n".join((b.get("text") or "") for b in blocks)
    for c in clauses:
        c["_n"] = norm(c["text"])
        if full[c["start"]:c["end"]] != c["text"]:
            r.i0_bad.append(c["path"])

    # 主键重复：path 是"编号路径"，同一份文件里 通知正文 与 附件办法 可能各有「一、」
    # 2026-11-14：附录切分后同一 path 会有多个 sec，path 单独不再能判重
    paths = Counter((c.get("part", 0), c["path"], c.get("sec", 0)) for c in clauses)
    r.dup_path = sum(n for n in paths.values() if n > 1)
    r.dup_kinds = sum(1 for n in paths.values() if n > 1)

    # 结构父：按 level 栈找"最近的更粗一级"，而不是按 path 查表
    # （path 会重复，用 path 当主键会把不同章节的父条款认错——第一版体检就是这么误报 1791 条的）
    stack: list[dict] = []
    kids: dict[int, list[dict]] = defaultdict(list)
    cur_part = None
    for c in sorted(clauses, key=lambda x: (x.get("part", 0), x["start"])):
        # 换了一份文档（正文 → 附件里的《办法》）编号会重新开始，父子栈必须清空
        if c.get("part", 0) != cur_part:
            stack.clear()
            cur_part = c.get("part", 0)
        while stack and stack[-1]["level"] >= c["level"]:
            stack.pop()
        c["_parent"] = stack[-1] if stack else None
        if stack:
            kids[id(stack[-1])].append(c)
        stack.append(c)

    for c in clauses:
        # I1 标签自洽
        # 例外：附录被按 600 字切块，后续块的文字不以「附件N」开头，这是设计而非缺陷
        is_app = c["label"].startswith(("附件", "附录", "附表")) and c.get("sec", 0) > 0
        if not is_app and not c["_n"].startswith(norm(c["label"])):
            r.i1_bad.append(c["path"])
        # I6 / I7
        n = len(c["_n"])
        if n < MIN_CHARS:
            r.i6_bad += 1
        if n > MAX_CHARS:
            r.i7_bad += 1
        p = c["_parent"]
        if p is not None:
            # I2 父含子
            if c["_n"] and c["_n"] not in p["_n"]:
                r.i2_bad.append(c["path"])
            # I3 区间落在父区间内
            if not (p["start"] <= c["start"] and c["end"] <= p["end"]):
                r.i3_bad.append(c["path"])

    # I4 兄弟不重叠
    for group in kids.values():
        group.sort(key=lambda c: c["start"])
        for a, b in zip(group, group[1:]):
            if b["start"] < a["end"]:
                r.i4_bad.append(b["path"])

    # I5 覆盖：叶子区间并集
    leaves = [c for c in clauses if id(c) not in kids]
    spans = sorted((c["start"], c["end"]) for c in leaves)
    covered, cur_s, cur_e = 0, None, None
    for s, e in spans:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            covered += cur_e - cur_s
            cur_s, cur_e = s, e
    if cur_s is not None:
        covered += cur_e - cur_s
    r.i5_gap = max(0.0, 1 - covered / total) if total else 0.0

    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    reports = []
    for f in sorted(DATA.glob("*.json")):
        rec = json.loads(f.read_text("utf-8"))
        if not rec.get("blocks"):
            continue
        reports.append(check(rec))

    if a.json:
        Path("data/reports/segmentation.json").write_text(
            json.dumps([r.__dict__ | {"density": round(r.density, 2)} for r in reports],
                       ensure_ascii=False, indent=1), "utf-8")
        return

    by = defaultdict(list)
    for r in reports:
        by[r.carrier].append(r)

    print(f"文档 {len(reports)} 份｜条款 {sum(r.clauses for r in reports):,} 条\n")
    head = ("载体", "份数", "条款", "I0偏移", "I1标签", "I2父含子", "I3区间", "I4重叠", "I6碎片", "I7粘连", "path重复")
    print("{:<10}{:>5}{:>7}{:>8}{:>8}{:>9}{:>8}{:>8}{:>8}{:>8}{:>12}".format(*head))
    for k in sorted(by, key=lambda x: -len(by[x])):
        g = by[k]
        print("{:<10}{:>5}{:>7}{:>8}{:>8}{:>9}{:>8}{:>8}{:>8}{:>8}{:>10}".format(
            k, len(g), sum(r.clauses for r in g),
            sum(len(r.i0_bad) for r in g),
            sum(len(r.i1_bad) for r in g), sum(len(r.i2_bad) for r in g),
            sum(len(r.i3_bad) for r in g), sum(len(r.i4_bad) for r in g),
            sum(r.i6_bad for r in g), sum(r.i7_bad for r in g),
            f"{sum(r.dup_path for r in g):,}"))

    bad = [r for r in reports if r.i0_bad or r.i1_bad or r.i2_bad or r.i3_bad or r.i4_bad]
    print(f"\n有硬违规的文档 {len(bad)} 份")
    for r in sorted(bad, key=lambda r: -(len(r.i1_bad) + len(r.i2_bad) + len(r.i3_bad) + len(r.i4_bad)))[:a.top]:
        print(f"  {r.doc} [{r.carrier}] 条款{r.clauses} I0={len(r.i0_bad)} I1={len(r.i1_bad)} I2={len(r.i2_bad)} "
              f"I3={len(r.i3_bad)} I4={len(r.i4_bad)}")
        for item in (r.i1_bad[:2] or r.i2_bad[:2] or r.i3_bad[:2]):
            print(f"      · {item}")

    holes = sorted(reports, key=lambda r: -r.i5_gap)[:5]
    print("\n覆盖空洞最大的 5 份")
    for r in holes:
        if r.i5_gap > 0:
            print(f"  {r.doc} [{r.carrier}] 空洞 {r.i5_gap:.1%} 条款{r.clauses} 字{r.chars}")

    frag = sum(r.i6_bad for r in reports)
    stick = sum(r.i7_bad for r in reports)
    dup = sum(r.dup_path for r in reports)
    dup_docs = sum(1 for r in reports if r.dup_path)
    off = sum(len(r.i0_bad) for r in reports)
    print(f"\n偏移不一致 {off} 条｜碎片段 {frag} 条｜粘连段 {stick} 条｜path 重复 {dup} 条（{dup_docs} 份文档）")


if __name__ == "__main__":
    main()
