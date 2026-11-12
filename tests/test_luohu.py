"""落户评分：四态边界、区间、封顶、互斥。"""
from __future__ import annotations

from app.judgement.luohu import evaluate, load_rules

BASE = dict(
    degree="master", school_tier=1, school_in_shanghai=True, gpa_rank="a",
    foreign="cet6_or_tem8", computer="grad", employer_contract=True,
    honor_national=False, honor_province=False, honor_school=False,
    competition="none", patent=False, service_west=False,
)


def test_phd_goes_direct_without_scoring():
    v = evaluate({"degree": "phd"})
    assert v.verdict == "direct"
    assert "博士" in v.direct


def test_master_max_basic_is_one_point_short():
    """24+15+2+8+8+7+7 = 71，比 72 少 1 分——这是规则本身的事实，不是 bug。"""
    v = evaluate(BASE)
    assert v.total_known == 71
    assert v.verdict == "unknown"      # 不是 unsatisfied：还有未知项
    assert v.gap == 1


def test_one_national_honor_pushes_over():
    v = evaluate({**BASE, "honor_national": True})
    assert v.total_known == 81
    assert v.verdict == "satisfied"


def test_low_bachelor_cannot_reach_and_we_dare_to_say_unsatisfied():
    low = dict(degree="bachelor", school_tier=3, school_in_shanghai=False, gpa_rank="d",
               foreign="none", computer="none", employer_contract=False,
               employer_key_field=False, employer_newtown_public=False,
               employer_major_project=False, employer_remote_public=False,
               employer_school=False, employer_pe_teacher=False,
               honor_national=False, honor_province=False, honor_school=False,
               competition="none", patent=False, service_west=False)
    v = evaluate(low)
    assert v.verdict == "unsatisfied"
    assert v.total_upper < 72


def test_honor_plus_competition_is_capped_at_15():
    v = evaluate({**BASE, "honor_national": True, "competition": "national_first"})
    assert v.total_known == 71 + 15          # 10 + 10 → 封顶 15
    assert any("封顶" in n or "不超过" in n for n in v.notes)


def test_missing_fact_is_unknown_not_zero():
    p = {k: v for k, v in BASE.items() if k != "degree"}
    v = evaluate(p)
    item = next(i for i in v.items if i.id == "degree.master")
    assert item.state == "unknown"
    assert v.verdict == "unknown"


def test_every_item_points_at_a_page():
    rules = load_rules()
    for g in rules["groups"]:
        for it in g["items"]:
            assert it["page"] >= 21, it["id"]
            assert it["points"] > 0


def test_rule_file_cites_the_document():
    meta = load_rules()["meta"]
    assert meta["doc_no"] == "沪教委学〔2026〕13号"
    assert meta["standard_score"] == 72
