"""副作用：唯一会改变外部世界的地方，所以只在这里做两件事——幂等、记账。

幂等键 = sha256(run_id | kind | payload)。同一个键第二次调用直接返回上次结果，
`applied=False`，不再写世界。这是"批准两次只创建一次"的实现点。

现在写的是本地 JSON（mock 世界），换成 MCP 工具时**只替换 `_write` 一个函数**。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _world() -> Path:
    return Path(os.environ.get("CLAUSELEDGER_WORLD", "data/world/actions.json"))


def key_of(run_id: str, kind: str, payload: dict) -> str:
    raw = json.dumps({"run": run_id, "kind": kind, "payload": payload},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _read() -> dict:
    p = _world()
    if not p.exists():
        return {"applied": {}}
    return json.loads(p.read_text("utf-8"))


def _write(data: dict) -> None:
    p = _world()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, p)


def apply(run_id: str, kind: str, payload: dict) -> dict:
    """执行一次副作用；**按条目幂等**，重复调用不新增。

    返回 `{"applied": 新增条数, "reused": 已存在条数, "keys": [...]}`。
    条目可能是多条（一次批准写 5 条待办），所以幂等粒度也是"条"而不是"批"。
    """
    items = payload.get("items") or [payload]
    data = _read()
    new = reused = 0
    keys = []
    for it in items:
        k = key_of(run_id, kind, it)
        keys.append(k)
        if k in data["applied"]:
            reused += 1
            continue
        data["applied"][k] = {"key": k, "run_id": run_id, "kind": kind, "payload": it}
        new += 1
    if new:
        _write(data)
    return {"applied": new, "reused": reused, "keys": keys}


def count(kind: str | None = None) -> int:
    rows = _read()["applied"].values()
    return sum(1 for r in rows if kind is None or r["kind"] == kind)
