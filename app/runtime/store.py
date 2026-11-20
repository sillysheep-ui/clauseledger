"""run 的持久化。默认 SQLite（单文件、事务、WAL），`CLAUSELEDGER_STORE=files` 回退到目录。

契约只有六个方法：`save/load/exists/event/events/list_runs`，外加只在这里用的
`commit`（状态 + 事件一个事务）。换成 Postgres 时替换本文件即可。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from . import db, events
from .state import RunState, now

ROOT = Path(os.environ.get("CLAUSELEDGER_RUNS", "data/runs"))


def backend() -> str:
    return os.environ.get("CLAUSELEDGER_STORE", "sqlite").lower()


class Store:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else ROOT

    @staticmethod
    def new_id() -> str:
        return "run_" + uuid.uuid4().hex[:12]

    # ---- 文件布局（仅 files 后端）----
    def dir(self, run_id: str) -> Path:
        return self.root / run_id

    def state_path_of(self, run_id: str) -> Path:
        return self.dir(run_id) / "state.json"

    def events_path_of(self, run_id: str) -> Path:
        return self.dir(run_id) / "events.jsonl"

    # ---- 写 ----
    def save(self, state: RunState) -> None:
        if backend() == "files":
            d = self.dir(state.run_id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path_of(state.run_id).with_suffix(".tmp")
            tmp.write_text(state.to_json(), "utf-8")
            os.replace(tmp, self.state_path_of(state.run_id))
            return
        with db.tx() as conn:
            db.upsert_run(conn, json.loads(state.to_json()))

    def commit(self, state: RunState, step: str, kind: str, payload: dict | None = None) -> dict:
        """状态与事件同一事务：要么都在，要么都不在。"""
        if backend() == "files":
            self.save(state)
            return self.event(state, step, kind, payload)
        with db.tx() as conn:
            db.upsert_run(conn, json.loads(state.to_json()))
            return db.append_event(conn, state.run_id, now(), step, kind, payload)

    def event(self, state: RunState, step: str, kind: str, payload: dict | None = None) -> dict:
        if backend() == "files":
            return events.append(self.events_path_of(state.run_id), state.run_id, step, kind, payload)
        with db.tx() as conn:
            db.upsert_run(conn, json.loads(state.to_json()))   # 事件里带的最新 step 也落库
            return db.append_event(conn, state.run_id, now(), step, kind, payload)

    # ---- 读 ----
    def load(self, run_id: str) -> RunState:
        if backend() == "files":
            return RunState.from_json(self.state_path_of(run_id).read_text("utf-8"))
        row = db.connect().execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunState.from_json(row["state"])

    def exists(self, run_id: str) -> bool:
        if backend() == "files":
            return self.state_path_of(run_id).exists()
        row = db.connect().execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row is not None

    def events(self, run_id: str) -> list[dict]:
        if backend() == "files":
            return events.read(self.events_path_of(run_id))
        rows = db.connect().execute(
            "SELECT seq,run_id,at,step,kind,payload FROM events WHERE run_id=? ORDER BY seq",
            (run_id,)).fetchall()
        return [dict(seq=r["seq"], run_id=r["run_id"], at=r["at"], step=r["step"],
                     kind=r["kind"], payload=json.loads(r["payload"])) for r in rows]

    def list_runs(self) -> list[dict]:
        if backend() == "files":
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
        rows = db.connect().execute(
            "SELECT run_id,status,step,question,updated_at FROM runs ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
