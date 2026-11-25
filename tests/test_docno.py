"""文号归一化：同一份文件的不同写法必须对得上。"""
from __future__ import annotations

from app.domain import validity as V
from app.domain.docno import key, same


def test_brackets_and_spaces_are_the_same_key() -> None:
    forms = [
        "沪人社养发(2011)66号",
        "沪人社养发（2011）66号",
        "沪人社养发〔2011〕66号",
        "沪人社养发〔2011〕 66 号",
        "沪人社养发〔2011〕第66号",
    ]
    assert len({key(f) for f in forms}) == 1
    assert all(same(forms[0], f) for f in forms[1:])


def test_two_digit_year_is_expanded() -> None:
    assert key("沪劳资发〔87〕130号") == "沪劳资发〔1987〕130号"
    assert key("沪劳保发〔95〕83号") == "沪劳保发〔1995〕83号"
    assert key("沪人社规〔26〕1号") == "沪人社规〔2026〕1号"      # 30 以下算 20xx
    assert key("沪人社规〔2026〕1号") == "沪人社规〔2026〕1号"    # 四位不动


def test_key_is_idempotent() -> None:
    for no in ["沪人社养发(2011)66号", "沪劳资发〔87〕130号", "沪人社规〔2021〕第26号"]:
        assert key(key(no)) == key(no)


def test_pick_falls_back_from_raw_to_key_and_alias() -> None:
    table = {"沪人社养发〔2011〕66号": [{"start": "2011-01-01", "end": ""}]}
    assert V._pick(table, "沪人社养发(2011)66号")            # 原文不同、键同
    assert V._pick({"x": []}, "y") == []


def test_alias_lookup(tmp_path, monkeypatch) -> None:
    import json

    p = tmp_path / "validity.json"
    p.write_text(json.dumps({
        "schema": 2,
        "periods": {"沪劳资发〔1987〕130号": [{"start": "1987-01-01", "end": "2031-08-15"}]},
        "alias": {"沪劳资发〔87〕130号": "沪劳资发〔1987〕130号"},
    }, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(V, "DERIVED", p)
    monkeypatch.setattr(V, "_PERIODS", None)
    assert V.periods_of("沪劳资发〔87〕130号")[0]["end"] == "2031-08-15"
