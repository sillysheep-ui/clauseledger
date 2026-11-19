"""置信度：三项加权，且"模型不可用"时要能退回旧规则。"""
import os

from app.retrieval import confidence as C
from app.retrieval.search import search_result


def test_score_is_monotonic():
    lo = C.score(lex=2.0, margin=0.2, cos=0.60)
    mid = C.score(lex=6.0, margin=1.0, cos=0.72)
    hi = C.score(lex=11.0, margin=3.0, cos=0.90)
    assert lo < mid < hi


def test_score_bounds():
    assert C.score(0, 0, 0) == 0.0
    assert C.score(999, 999, 1.0) == 1.0


def test_semantic_component_actually_counts():
    """只提高余弦、其他不变，分数必须上升——语义项不能被忽略。"""
    a = C.score(lex=5.0, margin=0.5, cos=0.60)
    b = C.score(lex=5.0, margin=0.5, cos=0.85)
    assert b > a


def test_threshold_env(monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_CONF_T", "0.99")
    assert C.threshold() == 0.99


def test_falls_back_when_disabled(monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_CONF", "0")
    assert C.cosine("失业保险金怎么领", "失业保险金按月发放") is None


def test_probe_threshold_one_rejects_everything(monkeypatch):
    """探针：阈值设到 1.01，任何问题都必须不敢下结论。

    单测环境默认关掉语义模型（CLAUSELEDGER_CONF=0），这里显式打开并伪造余弦，
    否则会退回旧判据、探针失去意义。
    """
    monkeypatch.setenv("CLAUSELEDGER_CONF", "1")
    monkeypatch.setenv("CLAUSELEDGER_CONF_T", "1.01")
    monkeypatch.setattr(C, "cosine", lambda q, t: 0.9)
    ans = search_result("失业保险金怎么领", k=5)
    assert ans.kind in ("no_direct_clause", "insufficient")
