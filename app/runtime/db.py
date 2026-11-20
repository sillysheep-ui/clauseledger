"""SQLite 后端：run 状态、事件流、副作用幂等键，一个文件、一次事务。

为什么不是 JSONL：`events.jsonl` 的序号来自"数行数"，两个请求同时 resume
同一个 run 时，两边都读到最后一行 seq=N，于是都写 N+1；`actions.apply()`
也是"读全表 → 算差集 → 写回"，并发下会重复执行。演示单线程看不出来，
而这正好是"可恢复 + 恰好一次"最该证明的地方。

事务边界：
- 一次 step 的 `state` + `event` 写在同一个事务里（`Store.commit`）；
- 副作用按**条**插入，主键就是幂等键，`INSERT OR IGNORE` 天然去重。

WAL 模式让读写不互相阻塞；`BEGIN IMMEDIATE` 让"取 seq 再插入"不会撞车。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("CLAUSELEDGER_DB", "data/runtime.db"))

_local = threading.local()
_WRITE_LOCK = threading.RLock()   # 同进程内串行化写事务，避免 SQLITE_BUSY

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id     TEXT PRIMARY KEY,
  status     TEXT NOT NULL,
  step       TEXT NOT NULL,
  question   TEXT NOT NULL,
  state      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  run_id  TEXT NOT NULL,
  seq     INTEGER NOT NULL,
  at      TEXT NOT NULL,
  step    TEXT NOT NULL,
  kind    TEXT NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS actions (
  key        TEXT PRIMARY KEY,
  run_id     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_run ON actions(run_id, kind);
"""


def path() -> Path:
    return Path(os.environ.get("CLAUSELEDGER_DB", DB_PATH))


def connect(p: str | Path | None = None) -> sqlite3.Connection:
    """进程内复用一条连接（sqlite3 默认禁止跨线程共享，所以按线程存）。"""
    target = str(p or path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "target", None) == target:
        return conn
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _local.conn, _local.target = conn, target
    return conn


@contextmanager
def tx(p: str | Path | None = None):
    """写事务：BEGIN IMMEDIATE 先把写锁拿到，再读 max(seq)。"""
    with _WRITE_LOCK:                       # 跨线程：进程内互斥；跨进程靠 WAL + busy timeout
        conn = connect(p)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def upsert_run(conn: sqlite3.Connection, state: dict) -> None:
    conn.execute(
        """INSERT INTO runs(run_id,status,step,question,state,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(run_id) DO UPDATE SET
             status=excluded.status, step=excluded.step, state=excluded.state,
             updated_at=excluded.updated_at""",
        (state["run_id"], state["status"], state["step"], state["question"],
         _dumps(state), state["created_at"], state["updated_at"]),
    )


def append_event(conn: sqlite3.Connection, run_id: str, at: str, step: str,
                 kind: str, payload: dict | None) -> dict:
    row = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                       (run_id,)).fetchone()
    seq = int(row[0])
    conn.execute("INSERT INTO events(run_id,seq,at,step,kind,payload) VALUES(?,?,?,?,?,?)",
                 (run_id, seq, at, step, kind, _dumps(payload or {})))
    return {"seq": seq, "run_id": run_id, "at": at, "step": step, "kind": kind,
            "payload": payload or {}}


def apply_items(run_id: str, kind: str, items: list[dict], at: str) -> dict:
    """按条幂等写入；主键冲突即"这条已经做过"。"""
    from .actions import key_of

    applied = reused = 0
    keys: list[str] = []
    with tx() as conn:
        for it in items:
            k = key_of(run_id, kind, it)
            keys.append(k)
            cur = conn.execute(
                "INSERT OR IGNORE INTO actions(key,run_id,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (k, run_id, kind, _dumps(it), at))
            if cur.rowcount:
                applied += 1
            else:
                reused += 1
    return {"applied": applied, "reused": reused, "keys": keys}


def world_items(kind: str | None = None) -> list[dict]:
    conn = connect()
    if kind:
        rows = conn.execute("SELECT payload FROM actions WHERE kind=? ORDER BY created_at", (kind,))
    else:
        rows = conn.execute("SELECT payload FROM actions ORDER BY created_at")
    return [json.loads(r["payload"]) for r in rows]
