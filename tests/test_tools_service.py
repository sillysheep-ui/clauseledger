"""工具层：与 pipeline 同一份证据、找不到就说不存在。"""
from __future__ import annotations

from app.tools import service as T


def test_evidence_matches_pipeline_assembly():
    from app.answer.context import assemble
    from app.retrieval.search import search_result

    q = "失业保险金怎么领"
    d = T.evidence(q)
    assert d["refused"] is False
    ev = assemble(q, search_result(q, k=5).hits)
    got = d["evidence"]
    assert [e["eid"] for e in got] == [e.eid for e in ev]
    assert [e["text"] for e in got] == [e.text for e in ev]
    assert all(e["context"] for e in got[:3])


def test_get_clause_found_and_missing():
    ok = T.get_clause("沪人社规〔2026〕18号")
    assert ok["found"] is True and ok["text"]
    miss = T.get_clause("沪人社规〔2099〕999号")
    assert miss["found"] is False


def test_doc_exists_blocks_invented_numbers():
    assert T.doc_exists("沪人社规〔2026〕18号")["exists"] is True
    assert T.doc_exists("沪府发〔2026〕落户办法")["exists"] is False
