from app.answer.context import Evidence
from app.answer.generate import Draft
from app.answer.verify import check


def ev():
    return [Evidence("E1", "c1", "沪人社规〔2026〕1号", "某通知", ["一、"], 3,
                     "失业保险金标准为 2340 元/月，最长不超过 24 个月。", "hit", "《沪人社规〔2026〕1号》一、（第 3 页）"),
            Evidence("E2", "c2", "沪人社规〔2026〕2号", "另一通知", ["二、"], 7,
                     "申领需提供身份证。", "hit", "《沪人社规〔2026〕2号》二、（第 7 页）")]


def test_valid_sentence_is_kept():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。", "cites": ["E1"]}])
    r = check(d, ev())
    assert len(r.kept) == 1 and r.ok


def test_missing_cite_is_dropped():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。"}])
    r = check(d, ev())
    assert not r.kept and "没有引用" in r.dropped[0]["why"]


def test_unknown_cite_is_dropped():
    d = Draft(sentences=[{"text": "标准为 2340 元/月。", "cites": ["E9"]}])
    r = check(d, ev())
    assert not r.kept and "不存在" in r.issues[0]


def test_number_not_in_evidence_is_dropped():
    d = Draft(sentences=[{"text": "标准为 9999 元/月。", "cites": ["E1"]}])
    r = check(d, ev())
    assert not r.kept and "9999" in r.issues[0]


def test_rules_numbers_are_allowed():
    d = Draft(sentences=[{"text": "按规则算得 71 分。", "cites": ["E1"]}])
    r = check(d, ev(), rules_text="已知 71 分 / 标准 72 分")
    assert r.kept, r.issues
