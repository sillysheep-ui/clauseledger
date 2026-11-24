"""纯条款装配：每份文件上限、预算、顺序。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.answer import context as C


def _hit(doc: str, i: int, chars: int = 100):
    clause = SimpleNamespace(id=f"{doc}#{i}", doc_no=doc, title=doc,
                             path=[f"第{i}条"], page=1, text="字" * chars,
                             citation=f"《{doc}》第{i}条", start=0, end=chars)
    return SimpleNamespace(clause=clause, child=None)


@pytest.fixture(autouse=True)
def _no_heading(monkeypatch):
    monkeypatch.setattr(C, "heading_for", lambda *a, **k: "")


def test_caps_items_per_document():
    hits = [_hit("A", i) for i in range(10)] + [_hit("B", i) for i in range(5)]
    ev = C.assemble_flat(hits, max_items=30, per_doc=3, max_chars=10_000)
    docs = [e.doc_no for e in ev]
    assert docs.count("A") == 3 and docs.count("B") == 3
    assert [e.doc_no for e in ev] == ["A", "A", "A", "B", "B", "B"]  # 保持输入顺序


def test_budget_skips_oversize_and_stops_at_items():
    hits = [_hit("A", i) for i in range(5)]
    ev = C.assemble_flat(hits, max_items=30, per_doc=30, max_chars=250)
    assert len(ev) == 2  # 第三条会超预算，后面的也超

    ev = C.assemble_flat(hits, max_items=3, per_doc=30, max_chars=10_000)
    assert len(ev) == 3
    assert [e.eid for e in ev] == ["E1", "E2", "E3"]


def test_empty_hits_give_empty_evidence():
    assert C.assemble_flat([], max_items=30, per_doc=8, max_chars=6000) == []
