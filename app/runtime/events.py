"""只追加的事件流：`run_events` 是审计真源，checkpoint 只是恢复源。

规矩（docs/FOUNDATION.md §2）：
- 只 append，不改不删；序号由文件行数决定；
- 每行一个 JSON，字段固定 `seq/run_id/at/step/kind/payload`；
- 两个来源不一致时以事件为准，并记一条 `warning`。
"""
from __future__ import annotations

import json
from pathlib import Path

from .state import now


def append(path: Path, run_id: str, step: str, kind: str, payload: dict | None = None) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = 1
    if path.exists():
        with path.open("rb") as f:
            seq = sum(1 for _ in f) + 1
    row = {"seq": seq, "run_id": run_id, "at": now(), "step": step, "kind": kind,
           "payload": payload or {}}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def steps_of(events: list[dict]) -> list[str]:
    """按顺序返回"开始过"的 step，用于验证重入没有跳过或重复。"""
    return [e["step"] for e in events if e["kind"] == "step_started"]
