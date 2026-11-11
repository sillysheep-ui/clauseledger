import json
from pathlib import Path

import pytest

from app.domain.coverage import CoverageIndex

DOC = {
    "schema": "coverage/v1", "corpus_version": None,
    "totals": {"cities_total": 333, "cities_reachable": 309, "cities_blocked": 24},
    "cities": {
        "上海": {"status": "reachable", "url": "https://www.shanghai.gov.cn/", "matters": {}},
        "海北": {"status": "blocked", "reason": "discovery_gap",
                 "detail": "我们没找到该城市的官方入口",
                 "caveat": "未接入不代表当地没有相关政策，只代表我们没能自动取到"},
    },
}


@pytest.fixture()
def idx(tmp_path: Path) -> CoverageIndex:
    p = tmp_path / "coverage.json"
    p.write_text(json.dumps(DOC, ensure_ascii=False), "utf-8")
    return CoverageIndex.load(p)


def test_blocked_city_cannot_trigger_retrieval(idx):
    c = idx.check("海北藏族自治州", "落户")
    assert c.status == "blocked"
    assert not c.allows_retrieval
    assert "不代表当地没有相关政策" in c.caveat


def test_reachable_city_but_matter_not_built(idx):
    c = idx.check("上海", "租房补贴")
    assert c.status == "partial"
    assert c.allows_retrieval
    assert "尚未建库" in c.message


def test_out_of_scope_city(idx):
    c = idx.check("纽约")
    assert c.status == "out_of_scope"
    assert not c.allows_retrieval


def test_disclosure_mentions_both_numbers(idx):
    text = idx.disclosure()
    assert "309/333" in text and "24" in text


def test_blocked_and_no_policy_are_different_sentences(idx):
    """这行测试守的是项目主张：'没查' 与 '查了没有' 不能长得一样。"""
    blocked = idx.check("海北", "落户").message
    no_policy = "已检索 12 份文件，未发现相关规定"
    assert "未接入" in blocked
    assert "未发现" not in blocked
    assert blocked != no_policy
