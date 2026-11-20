"""词表是数据，不是代码。这几条守着"改了文件就生效、不依赖调用顺序"。"""

import json

from app.retrieval import index, lexicon


def test_default_lexicon_loads():
    lex = lexicon.load(fresh=True)
    assert lex.segmentation, "分词词表不能为空"
    assert lex.question_words and lex.water
    assert lex.version >= 1


def test_lexicon_is_cached_but_reloadable():
    a = lexicon.load(fresh=True)
    b = lexicon.load()
    assert a is b, "同一路径同一 mtime 应该复用缓存"
    c = lexicon.reload()
    assert c.path == a.path


def test_env_override_wins(tmp_path, monkeypatch):
    p = tmp_path / "lex.json"
    p.write_text(json.dumps({
        "version": 99, "segmentation": ["测试词"], "question_words": ["啥"], "water": ["的"]
    }, ensure_ascii=False), "utf-8")
    monkeypatch.setenv("CLAUSELEDGER_LEXICON", str(p))
    lex = lexicon.reload()
    assert lex.version == 99
    assert lex.segmentation == ("测试词",)


def test_missing_file_degrades_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUSELEDGER_LEXICON", str(tmp_path / "nope.json"))
    lex = lexicon.reload()
    assert lex.segmentation == () and lex.water == frozenset()


def test_tokenize_does_not_depend_on_build_order():
    """以前只在 collect()/build() 里加用户词，直接调 tokenize 拿到的是被切碎的结果。"""
    index._setup(force=True)
    assert index.tokenize("失业保险") == ["失业保险"]
    assert index.tokenize("视同缴费") == ["视同缴费"]


def test_segmentation_words_are_not_question_words():
    lex = lexicon.load(fresh=True)
    overlap = set(lex.segmentation) & set(lex.question_words)
    assert not overlap, f"同一个词不能既当分词词又当疑问词：{overlap}"
