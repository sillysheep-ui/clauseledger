"""条款切分：四套编号体系，由粗到细。

政策文本的编号没有统一标准：规范性文件多用「第一条」，通知类多用「一、（一）1.」。
只认一种就会产生假阴性——M0 用「第X条」扫上海文件得到 0 条，实际有 29 条。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tools.ingest.parse import Block

NUM = "一二三四五六七八九十百零〇两"
LEVELS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(rf"^第[{NUM}]+章")),
    (2, re.compile(rf"^第[{NUM}]+节")),
    (3, re.compile(rf"^第[{NUM}]+条")),
    (4, re.compile(rf"^[{NUM}]+、")),
    (5, re.compile(rf"^[（(][{NUM}]+[）)]")),
    (6, re.compile(r"^\d{1,2}[.．、](?!\d)")),
    (7, re.compile(r"^[（(]\d{1,2}[）)]")),
    # 2026-11-14 补：⑴⑵⑶ 这类圈码、全角句点「1．」、阿拉伯「第1条」
    # 实测漏切 376 行「1．」+18 行「⑴」+少量「第1条」——它们被并进上一条，条款粒度直接变粗
    (7, re.compile(r"^[⑴-⒇]")),
    (3, re.compile(r"^第\s*\d{1,3}\s*[条章节]")),
]


@dataclass
class Clause:
    path: str
    level: int
    label: str
    text: str
    block_index: int
    char_offset: int
    page: int | None
    start: int
    end: int


def _match(line: str) -> tuple[int, str] | None:
    s = line.strip()
    for level, pat in LEVELS:
        if pat.match(s):
            m = pat.match(s)
            return level, (m.group(0) if m else s[:8])
    return None


def split_clauses(blocks: list[Block]) -> list[Clause]:
    # 1) 行级索引 + 全局偏移（引用定位要能回到原文的字符位置）
    lines: list[tuple[int, int, str, int | None]] = []  # block_idx, offset_in_block, text, page
    for b in blocks:
        for ln in b.text.split("\n"):
            if ln.strip():
                lines.append((b.index, 0, ln, b.page))
    pos, cursor = [], 0
    for i, (_, _, txt, _) in enumerate(lines):
        pos.append(cursor)
        cursor += len(txt) + 1

    heads: list[tuple[int, int, str, int]] = []  # line_idx, level, label, offset
    for i, (_, _, txt, _) in enumerate(lines):
        m = _match(txt)
        if m:
            heads.append((i, m[0], m[1], pos[i]))

    out: list[Clause] = []
    stack: list[tuple[int, str]] = []
    for k, (i, level, label, off) in enumerate(heads):
        end_line = len(lines)
        for j, lvl, _, _ in heads[k + 1:]:
            if lvl <= level:
                end_line = j
                break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, label))
        path = "/".join(lb for _, lb in stack)
        body = "\n".join(lines[t][2] for t in range(i, end_line))
        bidx, _, _, page = lines[i]
        out.append(Clause(path=path, level=level, label=label, text=body,
                          block_index=bidx, char_offset=lines[i][1], page=page,
                          start=off, end=pos[end_line - 1] + len(lines[end_line - 1][2])))
    return out
