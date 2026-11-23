"""HTTP 层：只做转发，不做判断。

四个端点就是全部契约：
    POST /api/runs                 建 run 并推进到"停住或结束"
    GET  /api/runs                 列表
    GET  /api/runs/{id}            快照（+ 事件流）
    POST /api/runs/{id}/resume     提交一个决定，继续推进

界面（S8-C）之后挂在这个文件下面；现在 `/` 只是一页占位，避免"没有界面"变成
"没有服务"。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.runtime import Engine, Store

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="ClauseLedger")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
store = Store()
engine = Engine(store)


class NewRun(BaseModel):
    question: str
    profile: dict = {}


class Decision(BaseModel):
    action: str
    by: str = ""


@app.post("/api/runs")
def create_run(body: NewRun) -> dict:
    state = engine.start(body.question, body.profile)
    state = engine.advance(state)
    return state.snapshot()


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return store.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, events: bool = False) -> dict:
    if not store.exists(run_id):
        raise HTTPException(404, "run not found")
    out = store.load(run_id).snapshot()
    if events:
        out["events"] = store.events(run_id)
    return out


@app.post("/api/runs/{run_id}/resume")
def resume(run_id: str, body: Decision) -> dict:
    if not store.exists(run_id):
        raise HTTPException(404, "run not found")
    try:
        state = engine.resume(run_id, {"action": body.action, "by": body.by})
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return state.snapshot()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """单页界面：提问 → 卡片 → 审批 → 审计时间线。"""
    return (STATIC / "index.html").read_text("utf-8")
