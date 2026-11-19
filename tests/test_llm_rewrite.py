"""LLM 改写层的单元测试（不联网）。

这一层的风险不在"模型会不会写错词"，而在**没有守卫**：编出来的词、数字、翻转的
否定、重复的扩展词，任何一条漏过去都会让检索变差，而且不报错。所以测试全部围绕
守卫与降级，不测试模型输出本身。
"""
from app.retrieval import llm_rewrite


def test_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(llm_rewrite, "_key", lambda: "")
    assert llm_rewrite.available() is False
    r = llm_rewrite.apply("失业金怎么领")
    assert r.terms == [] and r.backend == "unavailable"


def test_guard_drops_unknown_terms(monkeypatch):
    monkeypatch.setattr(llm_rewrite, "_df", lambda t: 0)
    assert llm_rewrite._guard("失业金怎么领", ["失业保险金"]) == []


def test_guard_drops_numeric_and_short(monkeypatch):
    monkeypatch.setattr(llm_rewrite, "_df", lambda t: 99)
    out = llm_rewrite._guard("2024年失业金能领多少", ["2024年", "保险", "失业保险金"])
    assert "2024年" not in out          # 数字不进扩展
    assert "保险" in out                # 正常词保留
    assert "失业保险金" in out


def test_guard_dedup_and_original(monkeypatch):
    monkeypatch.setattr(llm_rewrite, "_df", lambda t: 99)
    out = llm_rewrite._guard("失业保险金怎么领", ["失业保险金", "失业保险金", "申领"])
    assert out == ["申领"]              # 已在原句里的词不再加


def test_guard_negation_blocks_positive_action(monkeypatch):
    monkeypatch.setattr(llm_rewrite, "_df", lambda t: 99)
    out = llm_rewrite._guard("没交过社保能补缴吗", ["补缴", "社会保险"])
    assert "补缴" not in out            # 否定问句不加正向动作词
    assert "社会保险" in out


def test_cache_roundtrip(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(llm_rewrite, "CACHE", cache)
    monkeypatch.setattr(llm_rewrite, "_key", lambda: "sk-test")
    monkeypatch.setattr(llm_rewrite, "_df", lambda t: 99)
    calls = {"n": 0}

    def fake_ask(q):
        calls["n"] += 1
        return ["失业保险金"]

    monkeypatch.setattr(llm_rewrite, "_ask", fake_ask)
    r1 = llm_rewrite.apply("失业金怎么领")
    r2 = llm_rewrite.apply("失业金怎么领")
    assert r1.terms == ["失业保险金"] and r2.terms == ["失业保险金"]
    assert calls["n"] == 1               # 第二次命中缓存，不重复计费
    assert r2.hits[0].get("cached") is True


def test_failure_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_rewrite, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(llm_rewrite, "_key", lambda: "sk-test")

    def boom(q):
        raise OSError("network down")

    monkeypatch.setattr(llm_rewrite, "_ask", boom)
    r = llm_rewrite.apply("失业金怎么领")
    assert r.terms == [] and r.backend == "llm-failed"
