"""就地修复条款偏移：start 必须随文档顺序单调递增。

成因：载体模型把"主副本条款"和"补充副本条款"各自切分后再拼接，
偏移用块数累加（而不是全文行数），会差几行；2026-11 实测 18 份文档因此
start 非单调，父子区间检查随之失真。

只改 start/end/block_index/char_offset，不动文本与结构。

    python -m tools.ingest.fix_offsets            # 只看
    python -m tools.ingest.fix_offsets --write    # 写回
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.ingest.carriers import relocate
from tools.ingest.parse import Block

PROCESSED = Path("data/processed")


def _locate_blocks(blocks: list[dict], text: str) -> list[tuple[int, int]]:
    """返回每块在全文里的 [start, end)。"""
    spans, cur = [], 0
    for b in blocks:
        t = b.get("text") or ""
        spans.append((cur, cur + len(t)))
        cur += len(t) + 1  # "\n".join 的分隔符
    return spans


def fix(rec: dict) -> bool:
    blocks = rec.get("blocks") or []
    clauses = rec.get("clauses") or []
    if not blocks or not clauses:
        return False
    text = "\n".join(b.get("text") or "" for b in blocks)
    bl = [Block(b.get("i", i), b.get("kind", "para"), b.get("text") or "", b.get("page")) for i, b in enumerate(blocks)]

    class _C:  # 轻量壳，复用 relocate 的定位逻辑
        def __init__(self, c: dict):
            self.d = c
            self.text = c.get("text") or ""
            self.start = c.get("start") or 0
            self.end = c.get("end") or 0
            self.block_index = c.get("block_index") or 0
            self.char_offset = c.get("char_offset") or 0

    shells = [_C(c) for c in clauses]
    before = [c.get("start") for c in clauses]
    relocate(bl, shells)
    # 载体装配顺序（主副本 → 补充）不等于文档顺序；按定位结果重排，
    # 否则 JSON 里 start 会来回跳（父子检查、边界抽检都依赖单调）。
    pairs = sorted(zip(clauses, shells), key=lambda x: (x[1].start, x[1].end))
    clauses[:] = [c for c, _ in pairs]
    shells = [sh for _, sh in pairs]
    changed = False
    spans = _locate_blocks(blocks, text)
    for c, sh in zip(clauses, shells):
        if (sh.start, sh.end) != (c.get("start"), c.get("end")):
            changed = True
        c["start"], c["end"] = sh.start, sh.end
        # block_index / char_offset 跟着重算
        for i, (s, e) in enumerate(spans):
            if s <= sh.start < e + 1:
                c["block_index"], c["char_offset"] = i, sh.start - s
                break
    return changed or before != sorted(before)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    rows, changed, nonmono = [], 0, 0
    for p in sorted(PROCESSED.glob("*.json")):
        d = json.loads(p.read_text("utf-8"))
        cs = d.get("clauses") or []
        if not cs:
            continue
        st = [c.get("start") or 0 for c in cs]
        was_bad = st != sorted(st)
        nonmono += 1 if was_bad else 0
        if fix(d):
            changed += 1
            rows.append(d.get("doc_no"))
            if args.write:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
                os.replace(tmp, p)

    print(f"start 非单调 {nonmono} 份｜被修正 {changed} 份")
    for r in rows[:10]:
        print("  ·", r)
    if not args.write:
        print("（未写回，加 --write）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
