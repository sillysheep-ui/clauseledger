"""LLM 改写层（v2：只翻译"语料不认识的词"）的单元测试，不联网。

这一层的风险不在"模型会不会写错词"，而在**没有守卫**：编出来的词、数字、翻转的
否定、重复的扩展词，任何一条漏过去都会让检索变差，而且不报错。所以测试全部围绕
守卫、锚定与降级，不测试模型输出本身。
"""
from app.retrieval import llm_rewrite as L


def _fake_df(table):
    return lambda t: table.get(t, 0)


def test_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(L, "_key", lambda: "")
    assert L.available() is False
    r = L.apply("失业金怎么领")
    assert r.terms == [] and r.backend == "unavailable"


def test_guard_drops_terms_not_in_corpus(monkeypatch):
    monkeypatch.setattr(L, "_df", _fake_df({}))
    assert L._guard_terms("失业金怎么领", ["失业保险金"]) == []


def test_guard_drops_numeric_and_too_short(monkeypatch):
    monkeypatch.setattr(L, "_df", _fake_df({"保险": 9, "失业保险金": 90}))
    out = L._guard_terms("2024年失业金能领多少", ["2024年", "保险", "失业保险金"])
    assert "2024年" not in out          # 数字不进扩展
    assert out == ["保险", "失业保险金"]


def test_guard_dedup_and_skip_words_already_in_question(monkeypatch):
    monkeypatch.setattr(L, "_df", _fake_df({"失业保险金": 90, "申领": 136}))
    out = L._guard_terms("失业保险金怎么领", ["失业保险金", "失业保险金", "申领"])
    assert out == ["申领"]              # 原句已有的词不再加


def test_guard_negation_blocks_positive_action(monkeypatch):
    monkeypatch.setattr(L, "_df", _fake_df({"补缴": 5, "社会保险": 632}))
    out = L._guard_terms("没交过社保能补缴吗", ["补缴", "社会保险"])
    assert "补缴" not in out            # 否定问句不加正向动作词
    assert "社会保险" in out


def test_unknown_only_picks_tokens_corpus_does_not_know(monkeypatch):
    # 语料认识"社会保险"，不认识"生娃"
    monkeypatch.setattr(L, "_df", lambda t: 0 if t in ("生娃", "干零活") else 50)
    unk = L._unknown("生娃了社保怎么办")
    assert "生娃" in unk
    assert "社会保险" not in unk and "社保" not in unk


def test_no_unknown_skips_api_call(monkeypatch):
    monkeypatch.setattr(L, "_key", lambda: "sk-test")
    monkeypatch.setattr(L, "_unknown", lambda q: [])
    called = {"n": 0}
    monkeypatch.setattr(L, "_post", lambda *a, **k: called.update(n=called["n"] + 1) or {})
    r = L.apply("失业保险金怎么领")
    assert r.terms == [] and called["n"] == 0      # 没有要翻译的词，不花这次调用


def test_mapping_anchored_to_unknown_words(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(L, "_key", lambda: "sk-test")
    monkeypatch.setattr(L, "_unknown", lambda q: ["生娃"])
    monkeypatch.setattr(L, "_df", _fake_df({"生育": 47, "失业保险金": 90}))
    # 模型返回了一个不在"要翻译清单"里的映射，必须被丢掉
    monkeypatch.setattr(L, "_ask_v2",
                        lambda q, u: [("生娃", ["生育"]), ("失业保险金", ["失业保险金"])])
    r = L.apply("生娃了怎么办")
    assert r.terms == ["生育"]
    assert r.hits[0]["from"] == "生娃"


def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(L, "_key", lambda: "sk-test")
    monkeypatch.setattr(L, "_unknown", lambda q: ["生娃"])
    monkeypatch.setattr(L, "_df", _fake_df({"生育": 47}))
    calls = {"n": 0}

    def fake(q, u):
        calls["n"] += 1
        return [("生娃", ["生育"])]

    monkeypatch.setattr(L, "_ask_v2", fake)
    r1 = L.apply("生娃能休几天")
    r2 = L.apply("生娃能休几天")
    assert r1.terms == ["生育"] and r2.terms == ["生育"]
    assert calls["n"] == 1               # 第二次命中缓存，不重复计费
    assert r2.hits[0].get("cached") is True


def test_failure_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(L, "_key", lambda: "sk-test")
    monkeypatch.setattr(L, "_unknown", lambda q: ["生娃"])

    def boom(q, u):
        raise OSError("network down")

    monkeypatch.setattr(L, "_ask_v2", boom)
    r = L.apply("生娃能休几天")
    assert r.terms == [] and r.backend == "llm-failed"
