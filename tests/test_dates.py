from datetime import date

from tools.ingest.dates import effective_range, extract, sign_date, status_of


def blk(text: str, i: int = 0) -> dict:
    return {"i": i, "kind": "para", "page": None, "text": text}


def test_sign_block_strict():
    """日期独立成块、上一块是机关名。"""
    blocks = [blk("正文……"), blk("上海市人力资源和社会保障局"), blk("2021年4月29日")]
    got = sign_date(blocks)
    assert got.date == "2021-04-29"
    assert got.source == "sign_block"


def test_sign_inline():
    """机关名与日期挤在同一块。"""
    blocks = [blk("正文……"), blk("上海市人力资源和社会保障局 2021年4月29日")]
    got = sign_date(blocks)
    assert got.date == "2021-04-29"
    assert got.source == "sign_inline"


def test_print_date_is_not_issued():
    """末尾的"印发日期"不能被当成成文日期。"""
    blocks = [
        blk("正文……"),
        blk("上海市教育委员会"),
        blk("2026年4月22日"),
        blk("上海市教育委员会办公室"),
        blk("2026年4月27日印发"),
    ]
    got = sign_date(blocks)
    assert got.date == "2026-04-22", got.raw
    assert got.source == "sign_block"


def test_no_signature():
    """完全无落款 → None + unknown。"""
    got = sign_date([blk("这是一份没有落款的短通知。")])
    assert got.date is None
    assert got.source == "unknown"


def test_effective_from():
    r = effective_range("本通知自2021年5月1日起施行。")
    assert r.effective_from == "2021-05-01"
    assert r.effective_to is None


def test_effective_to():
    r = effective_range("本通知自2021年5月1日起施行，有效期至2022年4月30日。")
    assert r.effective_from == "2021-05-01"
    assert r.effective_to == "2022-04-30"
    assert r.source == "text"


def test_long_term():
    r = effective_range("本办法长期有效。")
    assert r.effective_to is None
    assert r.source == "long_term"


def test_spaced_date():
    r = effective_range("自 2026 年 4 月 22 日起施行")
    assert r.effective_from == "2026-04-22"


def test_conflict_keeps_both():
    """列表日期 04-27、落款 04-22：两个都留，不猜哪个对。"""
    doc = {
        "meta_hint": {"issued_at": "2026-04-27"},
        "blocks": [blk("上海市教育委员会"), blk("2026年4月22日")],
    }
    got = extract(doc)
    assert got.issued_at == "2026-04-22"
    assert got.published_at == "2026-04-27"
    assert "不一致" in got.date_note


def test_status_boundary_same_day_is_effective():
    """到期当天仍算有效。"""
    assert status_of("2021-01-01", "2022-04-30", today=date(2022, 4, 30)) == "effective"
    assert status_of("2021-01-01", "2022-04-30", today=date(2022, 5, 1)) == "expired"


def test_unknown_is_not_expired():
    """没有有效期不等于过期。"""
    assert status_of(None, None) == "unknown"
