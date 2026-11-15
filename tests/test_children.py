"""父子索引：子块切分与回填父条款。"""
import json

from app.retrieval.children import MAX, MIN, split_children
from app.retrieval.index import build_children
from app.retrieval.records import Clause


def _mk(text, i=0):
    return Clause(id=f"x#{i}", doc_no="沪人社规〔2020〕1号", title="测试",
                  path=["一、"], level=1, label="一、", text=text)


def test_short_clause_is_one_child():
    text = "职工依法享受丧假三天。"
    assert split_children(text) == [(0, len(text))]


def test_long_clause_children_cover_text_without_gaps():
    text = "".join(f"第{i}项内容说明，涉及补贴标准和申请条件。" for i in range(40))
    spans = split_children(text)
    assert len(spans) > 1
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert s1 < e1 <= s2          # 相邻不重叠、不留空
    assert all(e - s <= MAX for s, e in spans)
    assert "".join(text[s:e] for s, e in spans) == text


def test_long_single_sentence_is_hard_split():
    text = "这是一句没有任何句号的很长的话" * 100
    spans = split_children(text)
    assert len(spans) > 1
    assert all(e - s <= MAX for s, e in spans)
    assert "".join(text[s:e] for s, e in spans) == text


def test_tail_shorter_than_min_is_merged():
    body = "内容。" * 110
    text = body + "尾"
    spans = split_children(text)
    assert spans[-1][1] == len(text)


def test_empty_text_has_no_children():
    assert split_children("") == []
    assert split_children("   ") == []


def test_children_point_back_to_their_parent():
    clauses = [_mk("短条款。"), _mk("内容。" * 300, 1)]
    kids = build_children(clauses)
    assert {k["pid"] for k in kids} == {0, 1}
    for k in kids:
        parent = clauses[k["pid"]]
        assert k["text"] == parent.text[k["start"]:k["end"]]
    assert len([k for k in kids if k["pid"] == 1]) > 1
