"""生成接进卡片：只解释、不改判定；拒答卡不生成；数字对不上就丢句。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_WORLD", str(tmp_path / "world.json"))
    monkeypatch.setenv("CLAUSELEDGER_GENERATE", "1")
    from app.answer import generate as G
    from app.api import server
    from app.runtime import Engine, Store

    server.store = Store(tmp_path / "runs")
    server.engine = Engine(server.store)
    # 不真的调 API：返回一段带引用的话，其中一句的数字是编的
    def fake_generate(question, ev, **kw):
        return G.Draft(sentences=[
            {"text": "失业保险金按累计缴费年限确定领取期限。", "cites": [ev[0].eid]},
            {"text": "领取期限最长不超过 99 个月。", "cites": [ev[0].eid]},
        ], confidence="high", model="fake")

    monkeypatch.setattr(G, "available", lambda: True)
    monkeypatch.setattr(G, "generate", fake_generate)
    return TestClient(server.app)


def test_draft_attached_keeps_verdict(client):
    r = client.post("/api/runs", json={"question": "失业保险金能领多久", "profile": {}}).json()
    card = r["answer"]["card"]
    assert card["kind"] == "standard"
    assert card["draft"]["sentences"], "生成结果应挂在卡上"
    # 判定字段没有被模型覆盖：卡还是那张卡
    assert card["headline"]
    # 编出来的"99 个月"必须被丢掉
    kept = "".join(s["text"] for s in card["draft"]["sentences"])
    assert "99 个月" not in kept
    assert card["draft"]["dropped"] >= 1


def test_refusal_never_generated(client):
    r = client.post("/api/runs", json={"question": "北京落户需要几年社保", "profile": {}}).json()
    card = r["answer"]["card"]
    assert card["kind"] == "refusal"
    assert "draft" not in card
