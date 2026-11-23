"""接口层冒烟：不测业务，只测"四个端点接上了、状态能流转"。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    from app.api import server
    from app.runtime import Engine, Store
    server.store = Store(tmp_path / "runs")
    server.engine = Engine(server.store)
    return TestClient(server.app)


def test_create_run_stops_at_gate(client):
    r = client.post("/api/runs", json={"question": "硕士毕业落户能加多少分",
                                       "profile": {"degree": "master"}}).json()
    assert r["status"] == "waiting"
    assert r["gate"]["kind"] == "judgement"
    assert r["hits"][0]["citation"]


def test_resume_advances_to_done(client):
    r = client.post("/api/runs", json={"question": "硕士毕业落户能加多少分",
                                       "profile": {"degree": "master"}}).json()
    r2 = client.post(f"/api/runs/{r['run_id']}/resume",
                     json={"action": "confirm_unknown", "by": "tester"}).json()
    assert r2["status"] == "done"
    assert r2["judgement"]["confirmed"] is True


def test_resume_on_done_is_409(client):
    r = client.post("/api/runs", json={"question": "硕士毕业落户能加多少分",
                                       "profile": {"degree": "master"}}).json()
    client.post(f"/api/runs/{r['run_id']}/resume", json={"action": "confirm_unknown"})
    again = client.post(f"/api/runs/{r['run_id']}/resume", json={"action": "approve"})
    assert again.status_code == 409


def test_events_visible(client):
    r = client.post("/api/runs", json={"question": "硕士毕业落户能加多少分",
                                       "profile": {"degree": "master"}}).json()
    got = client.get(f"/api/runs/{r['run_id']}?events=1").json()
    assert got["events"][0]["kind"] == "run_created"
    assert any(e["kind"] == "gate_opened" for e in got["events"])


def test_page_served(client):
    """界面本身：单页 + 静态资源。不测样式，只测"能打开、引用对"。"""
    page = client.get("/")
    assert page.status_code == 200
    assert "ClauseLedger" in page.text
    assert "/static/app.js" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_gate_shows_items_before_approval(client):
    """审批前必须能看见"将要写入什么"，否则是盲批。"""
    r = client.post("/api/runs", json={"question": "博士毕业落户能加多少分",
                                       "profile": {"degree": "phd"}}).json()
    assert r["gate"]["kind"] == "action"
    items = r["gate"]["payload"]["action"]["items"]
    assert items and all(i["doc_no"] and i["text"] for i in items)
    # 卡也要在停住之前就给出，不能让人看着空白批
    assert (r["answer"].get("card") or {}).get("kind") == "verdict"
