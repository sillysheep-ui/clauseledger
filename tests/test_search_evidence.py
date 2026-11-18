"""拒答必须能自证"我查过"——没查就说"没有这条政策"是本系统最不能犯的错。"""

from app.retrieval.search import search_result


def test_result_carries_search_evidence():
    """不论下没下结论，只要检索过，就必须带检索痕迹（版本、份数、条款数）。"""
    a = search_result("丧假有几天")
    assert a.kind in {"ok", "no_direct_clause"}, a.reason
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


def test_outcome_is_explicit():
    """六类结局必须显式：answer / no_direct_clause / not_covered / out_of_domain / insufficient。

    `no_direct_clause` 是 2026-11-17 加的：查到了候选、但没有一条能当答案。
    把它算进 answer 会高估准确率，算进 refused 又会丢掉候选。
    """
    assert search_result("丧假有几天").outcome in {"answer", "no_direct_clause"}
    assert search_result("北京落户要多少分").outcome in {
        "not_covered", "out_of_domain", "insufficient"
    }
