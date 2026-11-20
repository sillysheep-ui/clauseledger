"""副作用：唯一会改变外部世界的地方，所以只在这里做两件事——幂等、记账。

幂等键 = sha256(run_id | kind | payload)。同一个键第二次调用直接返回上次结果，
`applied=False`，不再写世界。这是"批准两次只创建一次"的实现点。

现在写的是本地 JSON（mock 世界），换成 MCP 工具时**只替换 `_write` 一个函数**。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _world() -> Path:
    return Path(os.environ.get("CLAUSELEDGER_WORLD", "data/world/actions.json"))


def _use_db() -> bool:
    return os.environ.get("CLAUSELEDGER_STORE", "sqlite").lower() != "files"


def _mirror() -> None:
    """把库里的条目导出到 mock 世界文件（人看、CLI 打印用）。"""
    from . import db
    try:
        items = db.world_items()
        _write({"applied": {str(i.get("key") or n): i for n, i in enumerate(items)}})
    except Exception:  # noqa: BLE001 - 镜像只是给人看的，失败不影响幂等与落库
        return


def now() -> str:
    from .state import now as _now
    return _now()


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
    # 并发下不能共用同一个 .tmp 名：两个线程会互相 rename 掉对方的临时文件
    fd, tmp = tempfile.mkstemp(prefix=p.name, suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply(run_id: str, kind: str, payload: dict) -> dict:
    """执行一次副作用；**按条目幂等**，重复调用不新增。

    返回 `{"applied": 新增条数, "reused": 已存在条数, "keys": [...]}`。
    条目可能是多条（一次批准写 5 条待办），所以幂等粒度也是"条"而不是"批"。

    SQLite 后端把"幂等键"放进 `actions` 表，靠主键冲突去重（一个事务）；
    `data/world/actions.json` 只是给人看的镜像，从库里导出，不承担判定。
    """
    items = payload.get("items") or [payload]
    if _use_db():
        from . import db
        res = db.apply_items(run_id, kind, items, now())
        _mirror()
        return res
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
    if _use_db():
        from . import db
        return len(db.world_items(kind))
    rows = _read()["applied"].values()
    return sum(1 for r in rows if kind is None or r["kind"] == kind)
