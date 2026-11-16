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

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.runtime import Engine, Store

app = FastAPI(title="ClauseLedger")
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
    return """<!doctype html><meta charset=utf-8><title>ClauseLedger</title>
<style>body{font:15px/1.7 -apple-system,sans-serif;max-width:760px;margin:48px auto;padding:0 16px}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style>
<h1>ClauseLedger · 条例账本</h1>
<p>服务已起。界面（S8-C）还没做，现在只有接口：</p>
<pre><code>POST /api/runs                {"question": "…", "profile": {}}
GET  /api/runs/{id}?events=1
POST /api/runs/{id}/resume    {"action": "approve|deny|confirm_unknown|reject"}</code></pre>
<p>交互式文档：<a href="/docs">/docs</a></p>"""
