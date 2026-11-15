"""父子索引里的"子块"：把一条长条款切成可独立检索的小段。

为什么需要：BM25 的分数对长度敏感——一条 17,799 字的条款，里面那句话的词频
被几万字稀释，长度归一化再扣一次分，结果就是"长条款几乎检索不到"（实测：
top-1 长度 >1000 字的命中数为 0）。切小是为了让信号不被稀释。

为什么只切不需要切：小于 MAX 的条款原样一条，不切。切细了却不回填父块，
就是把一句话切成两半、两边都不完整——那是碎片化，不是改进。

切分规则刻意简单：按句号/分号/换行断句，贪心装到 TARGET 字，单句超过 MAX
时才硬切。不做语义切分——2410.13070 的结论是语义切块没有稳定收益，
而按标点断句至少可解释：每个子块的边界都能在原文字符串里指出来。
"""
from __future__ import annotations

import re

TARGET = 300   # 装到这么多字就断一个子块
MAX = 500      # 单个子块的上限（单句超长时才会触及）
MIN = 120      # 小于这么多字的尾巴并进上一块

# 句末标点：中文句号、问号、叹号、分号，以及换行。保留标点在块尾。
SENT = re.compile(r"[^。！？；\n]*[。！？；\n]|[^。！？；\n]+$")


def _sentences(text: str) -> list[tuple[int, int]]:
    """返回每个句子的 (start, end)。空行不单独成句。"""
    out = []
    for m in SENT.finditer(text):
        if m.group().strip():
            out.append((m.start(), m.end()))
    return out


def split_children(text: str, *, target: int = TARGET, maximum: int = MAX,
                   minimum: int = MIN) -> list[tuple[int, int]]:
    """把条款正文切成 [(start, end)] 区间。短条款返回整段一个区间。"""
    text = text or ""
    if not text.strip():
        return []
    if len(text) <= maximum:
        return [(0, len(text))]

    spans: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    for s, e in _sentences(text):
        if cur_start is None:
            cur_start, cur_end = s, e
        elif e - cur_start <= maximum and cur_end - cur_start < target:
            cur_end = e
        else:
            spans.append((cur_start, cur_end))
            cur_start, cur_end = s, e
        # 单句本身超过上限：在块内硬切，优先切在逗号后
        while cur_end - cur_start > maximum:
            cut = cur_start + maximum
            seg = text[cur_start:cut]
            k = max(seg.rfind("，"), seg.rfind("、"))
            if k > maximum // 2:
                cut = cur_start + k + 1
            spans.append((cur_start, cut))
            cur_start = cut
    if cur_start is not None:
        if spans and cur_end - cur_start < minimum:
            spans[-1] = (spans[-1][0], cur_end)   # 尾巴并进上一块
        else:
            spans.append((cur_start, cur_end))
    return [(s, e) for s, e in spans if text[s:e].strip()]
