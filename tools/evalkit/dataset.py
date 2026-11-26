"""数据集加载：只做"读 + 过滤"，不做任何打分。"""
from __future__ import annotations

import json
from pathlib import Path

QFILE = Path("eval/questions_v3.json")
MISSING = Path("eval/questions_missing.json")
ANSWERS = Path("eval/official_answers.json")
PARAPHRASE = Path("eval/questions_paraphrase.json")


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def answers() -> dict[str, str]:
    return json.loads(ANSWERS.read_text(encoding="utf-8"))


def official(split: str | None = None, limit: int | None = None) -> list[dict]:
    rows = [q for q in _load(QFILE) if q.get("category") == "official"]
    if split:
        rows = [q for q in rows if q.get("split") == split]
    rows.sort(key=lambda q: q["id"])
    return rows[:limit] if limit else rows


def out_of_scope(split: str | None = None) -> list[dict]:
    rows = [q for q in _load(QFILE) if q.get("category") == "out_of_scope"]
    if split:
        rows = [q for q in rows if q.get("split") == split]
    return sorted(rows, key=lambda q: q["id"])


def in_domain_no_policy(split: str | None = None) -> list[dict]:
    rows = _load(MISSING)
    if split:
        rows = [q for q in rows if q.get("split") == split]
    return sorted(rows, key=lambda q: q["id"])


def paraphrase(limit: int | None = None) -> list[dict]:
    rows = sorted(_load(PARAPHRASE), key=lambda q: q["id"])
    return rows[:limit] if limit else rows


def text_of(q: dict) -> str:
    """一律用补过主语的问题文本，避免拿裸问句测出虚低的数字。"""
    return q.get("q_ctx") or q["q"]
