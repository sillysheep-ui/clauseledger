"""效力判定的测试：重点不是"能算出来"，而是"能算对那一条让 9 道题判错的记录"。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain import validity

REL = Path("data/registry/relations.jsonl")
DOCS = Path("data/processed")


def _doc(doc_no: str) -> dict:
    for p in DOCS.glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        if d.get("doc_no") == doc_no:
            return d
    pytest.skip(f"语料里没有 {doc_no}")


needs_corpus = pytest.mark.skipif(
    not REL.exists(), reason="先跑 python -m tools.ingest.relations")


@needs_corpus
def test_key_repeal_is_caught():
    """沪人社福发〔2015〕34号 自身没写失效日期，但被 2020〕26号 明文废止。"""
    v = validity.status_at(_doc("沪人社福发〔2015〕34号"), as_of="2026-11-16")
    assert v.status == "repealed"
    assert v.repealed_by == "沪人社规〔2020〕26号"
    assert v.since == "2020-12-01"


@needs_corpus
def test_repeal_is_time_dependent():
    """废止生效前的那一天，它还是有效的——效力是时间的函数。"""
    v = validity.status_at(_doc("沪人社福发〔2015〕34号"), as_of="2020-06-01")
    assert v.status != "repealed"


def test_expired_by_own_date():
    d = {"doc_no": "X〔2020〕1号", "effective_from": "2020-01-01", "effective_to": "2020-12-31"}
    v = validity.status_at(d, as_of="2021-01-01", rels={})
    assert v.status == "expired"
    assert "2020-12-31" in v.reason


def test_no_end_date_is_unknown_not_effective():
    """没写有效期 ≠ 长期有效。检索保留、结论要标不确定。"""
    d = {"doc_no": "X〔2020〕2号", "effective_from": "2020-01-01", "effective_to": None}
    v = validity.status_at(d, as_of="2026-01-01", rels={})
    assert v.status == "unknown"


def test_partial_repeal_is_its_own_status():
    rels = {"X〔2019〕9号": [{"type": "partial_repeal", "source": "Y〔2021〕1号",
                            "effective_date": "2021-05-01"}]}
    d = {"doc_no": "X〔2019〕9号", "effective_from": "2019-01-01", "effective_to": "2029-01-01"}
    v = validity.status_at(d, as_of="2026-01-01", rels=rels)
    assert v.status == "partially_repealed"


def test_not_yet_effective_repeal_does_not_count():
    rels = {"X〔2019〕9号": [{"type": "repeal", "source": "Y〔2021〕1号",
                            "effective_date": "2027-01-01"}]}
    d = {"doc_no": "X〔2019〕9号", "effective_from": "2019-01-01", "effective_to": "2029-01-01"}
    v = validity.status_at(d, as_of="2026-01-01", rels=rels)
    assert v.status == "effective"


def test_repealed_map_keys_are_targets():
    m = validity.repealed_map(rels={"A": [{"type": "repeal", "source": "B",
                                          "effective_date": "2020-01-01"}]},
                              as_of="2026-01-01")
    assert "A" in m and "B" not in m


def test_year_of_ignores_numbers_inside_doc_no():
    from app.domain.validity import year_of
    assert year_of("2020年建筑业工伤保险怎么参加") == 2020
    assert year_of("沪人社规〔2020〕26号讲了什么") == 2020  # 文号里的年份也算时间线索
    assert year_of("比例是 2.5 的时候") is None
    assert year_of("赔偿 5000 元") is None


def test_in_force_respects_repeal_date():
    """2015〕34号 自身没写日期，但 2020-12-01 被废止：2019 有效、2021 无效。"""
    rels = {"X〔2015〕34号": [{"type": "repeal", "source": "Y〔2020〕26号",
                              "effective_date": "2020-12-01"}]}
    d = {"doc_no": "X〔2015〕34号", "effective_from": None, "effective_to": None}
    from app.domain.validity import in_force
    assert in_force(d, 2019, rels) is True
    assert in_force(d, 2020, rels) is True     # 那一年里前 11 个月有效
    assert in_force(d, 2021, rels) is False


def test_year_scope_changes_which_document_is_current():
    """同一个问题，加不加年份应当改变"哪份文件算数"的判断依据（不依赖检索结果）。"""
    rels = {"X〔2015〕34号": [{"type": "repeal", "source": "Y〔2020〕26号",
                              "effective_date": "2020-12-01"}]}
    old = {"doc_no": "X〔2015〕34号"}
    new = {"doc_no": "Y〔2020〕26号", "effective_from": "2020-12-01", "effective_to": "2025-11-30"}
    from app.domain.validity import in_force
    assert in_force(old, 2019, rels) and not in_force(new, 2019, rels)
    assert in_force(old, 2021, rels) is False and in_force(new, 2021, rels)
