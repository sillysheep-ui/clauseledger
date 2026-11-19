"""词表从数据里来，不从代码里来。

为什么要单独一个文件：分词词表是**会随语料变化**的东西——新增一个部门、换一批文件，
就可能多出新词（"育儿假" "随军" "选调生"），也可能发现某个旧词一次都没出现。
把它写死在 `index.py` 里，等于每次都要改代码、改测试、重新跑一遍评测，
而且没人能一眼看出"这张词表到底有没有用"。

三类词放一起，但**语义不同，不能混用**：

| 字段 | 作用 | 用错会怎样 |
|---|---|---|
| `segmentation` | 告诉 jieba"这是一个词，别切开" | 少了 → 词被切碎，检索不到；多了 → 无害但白加 |
| `question_words` | BM25 打分与覆盖率里**不参与**的词 | 不剔除 → "怎么" 这种词 idf 极高，把问答式条款顶上来 |
| `water` | 覆盖率**分母**里剔除的虚词 | 不剔除 → 问得越长覆盖率越低，闸门惩罚"问得具体" |

覆盖路径：`CLAUSELEDGER_LEXICON` 环境变量 > `data/fixtures/lexicon.json`。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("data/fixtures/lexicon.json")


@dataclass(frozen=True)
class Lexicon:
    segmentation: tuple[str, ...]
    question_words: frozenset[str]
    water: frozenset[str]
    colloquial: tuple[dict, ...] = ()
    version: int = 0
    path: str = ""

    def stats(self) -> dict:
        return {
            "segmentation": len(self.segmentation),
            "question_words": len(self.question_words),
            "water": len(self.water),
            "colloquial": len(self.colloquial),
            "version": self.version,
            "path": self.path,
        }


_cache: tuple[str, float, Lexicon] | None = None


def _path() -> Path:
    return Path(os.environ.get("CLAUSELEDGER_LEXICON") or DEFAULT_PATH)


def load(path: Path | None = None, *, fresh: bool = False) -> Lexicon:
    """读词表。按 (路径, mtime) 缓存——改完文件不用重启进程。"""
    global _cache
    p = Path(path) if path else _path()
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        # 文件丢了不能让整条检索链停摆：退回空词表，行为等同改造前的最小集。
        return Lexicon((), frozenset(), frozenset(), 0, str(p))
    if not fresh and _cache and _cache[0] == str(p) and _cache[1] == mtime:
        return _cache[2]
    raw = json.loads(p.read_text("utf-8"))
    lex = Lexicon(
        segmentation=tuple(dict.fromkeys(raw.get("segmentation") or ())),
        question_words=frozenset(raw.get("question_words") or ()),
        water=frozenset(raw.get("water") or ()),
        colloquial=tuple(raw.get("colloquial") or ()),
        version=int(raw.get("version") or 0),
        path=str(p),
    )
    _cache = (str(p), mtime, lex)
    return lex


def segmentation_words() -> list[str]:
    return list(load().segmentation)


def question_words() -> set[str]:
    return set(load().question_words)


def water() -> set[str]:
    return set(load().water)


def colloquial() -> list[dict]:
    """口语→政策词映射表（`rewrite` 层用）。"""
    return [dict(x) for x in load().colloquial]


def reload() -> Lexicon:
    return load(fresh=True)
