"""拒答必须能自证"我查过"——没查就说"没有这条政策"是本系统最不能犯的错。"""

from app.retrieval.search import search_result


def test_answered_result_carries_search_evidence():
    a = search_result("丧假有几天")
    assert a.kind == "ok", a.reason
    s = a.searched
    assert s["n_queries"] > 0 and s["n_docs"] > 0
    assert s["index_version"] and s["corpus_snapshot"]


def test_out_of_scope_never_looks_like_searched():
    a = search_result("北京落户要多少分")
    assert a.kind == "not_covered"
    assert a.searched["n_queries"] == 0, "没接入的城市不能留下'查过'的痕迹"


def test_insufficient_still_records_effort():
    a = search_result("公积金提取额度")
    if a.kind in {"not_covered", "out_of_domain"}:
        assert a.searched["n_queries"] == 0
    else:
        assert a.searched["n_queries"] > 0


def test_outcome_is_five_way():
    assert search_result("丧假有几天").outcome == "answer"
    assert search_result("北京落户要多少分").outcome in {
        "not_covered", "out_of_domain", "insufficient"
    }
