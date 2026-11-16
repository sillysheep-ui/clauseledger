"""run 的持久化：一个 run 一个目录，state.json + events.jsonl。

现在是文件存储（单机、可读、可 git diff），换成 Postgres 只替换本文件：
`save/load/list_runs` 三个函数就是全部契约。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from . import events
from .state import RunState  # noqa: F401  (类型提示用)

ROOT = Path(os.environ.get("CLAUSELEDGER_RUNS", "data/runs"))


def new_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


class Store:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else ROOT

    @staticmethod
    def new_id() -> str:
        import uuid
        return "run_" + uuid.uuid4().hex[:12]

    # ---- 路径 ----
    def dir(self, run_id: str) -> Path:
        return self.root / run_id

    @property
    def state_path(self) -> Path:  # pragma: no cover - 兼容旧调用
        raise AttributeError("用 store.state_path_of(run_id)")

    def state_path_of(self, run_id: str) -> Path:
        return self.dir(run_id) / "state.json"

    def events_path_of(self, run_id: str) -> Path:
        return self.dir(run_id) / "events.jsonl"

    # ---- 读写 ----
    def save(self, state: RunState) -> None:
        d = self.dir(state.run_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path_of(state.run_id).with_suffix(".tmp")
        tmp.write_text(state.to_json(), "utf-8")
        os.replace(tmp, self.state_path_of(state.run_id))   # 原子写：断电不会留半个文件

    def load(self, run_id: str) -> RunState:
        return RunState.from_json(self.state_path_of(run_id).read_text("utf-8"))

    def exists(self, run_id: str) -> bool:
        return self.state_path_of(run_id).exists()

    def event(self, state: RunState, step: str, kind: str, payload: dict | None = None) -> dict:
        return events.append(self.events_path_of(state.run_id), state.run_id, step, kind, payload)

    def events(self, run_id: str) -> list[dict]:
        return events.read(self.events_path_of(run_id))

    def list_runs(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            sp = d / "state.json"
            if sp.exists():
                s = json.loads(sp.read_text("utf-8"))
                out.append({"run_id": s["run_id"], "status": s["status"], "step": s["step"],
                            "question": s["question"], "updated_at": s["updated_at"]})
        return out
