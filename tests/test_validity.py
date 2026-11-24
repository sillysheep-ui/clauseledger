"""有效期链的解析规则。重点是「宁可不知道，也不要安错日期」。"""
from __future__ import annotations

from tools.ingest import validity as V


def doc(no, title, text, end=None):
    return {"doc_no": no, "title": title, "effective_to": end,
            "blocks": [{"text": text}]}


def test_single_date_extension_applies_to_all_listed_docs():
    a = doc("沪人社规〔2018〕46号", "关于印发《上海市工伤预防费使用管理试行办法》的通知", "正文", "2020-12-28")
    n = doc("沪人社规〔2020〕9号", "关于延长部分行政规范性文件有效期的通知",
            "经评估，下列文件需继续实施，有效期延长至2022年12月31日。"
            "上海市人力资源和社会保障局、上海市财政局关于印发《上海市工伤预防费使用管理试行办法》的通知")
    docs = {a["doc_no"]: a, n["doc_no"]: n}
    res = V.build(docs)
    assert res.valid_until[a["doc_no"]] == "2022-12-31"
    assert res.extended_by[a["doc_no"]] == [n["doc_no"]]


def test_multi_date_rows_pair_per_row():
    a = doc("沪人社规〔2018〕46号", "关于印发《上海市工伤预防费使用管理试行办法》的通知", "x", "2020-12-28")
    b = doc("沪人社规〔2018〕20号", "关于实施失业保险援企稳岗护航行动的通知", "x", "2021-12-31")
    n = doc("沪人社规〔2020〕9号", "关于延长部分行政规范性文件有效期的通知",
            "沪人社规〔2018〕20号 | 有效期至2025年12月31日"
            "沪人社规〔2018〕46号 | 有效期至2022年12月31日")
    docs = {a["doc_no"]: a, b["doc_no"]: b, n["doc_no"]: n}
    res = V.build(docs)
    assert res.valid_until[a["doc_no"]] == "2022-12-31"
    assert res.valid_until[b["doc_no"]] == "2025-12-31"


def test_column_major_with_missing_cell_is_left_unresolved():
    """列优先且**数量不齐**（OCR 丢掉一个日期）时不许硬配。

    数量相等的列优先是可以按顺序配的（`align_by_count`），这条守的是数量不等：
    少一个日期还按顺序硬配，会让第 3 个文件拿到第 4 个日期。
    """
    a = doc("沪人社规〔2018〕46号", "关于印发《上海市工伤预防费使用管理试行办法》的通知", "x", "2020-12-28")
    b = doc("沪人社规〔2018〕20号", "关于实施失业保险援企稳岗护航行动的通知", "x", "2021-12-31")
    n = doc("沪人社规〔2021〕8号", "关于延长部分社会保险类行政规范性文件有效期的通知",
            "文件名称关于实施失业保险援企稳岗护航行动的通知关于印发《上海市工伤预防费使用管理试行办法》的通知"
            "文号沪人社规〔2018〕20号沪人社规〔2018〕46号沪人社规〔2019〕40号"
            "延长有效期有效期至2025年12月31日有效期至2022年12月31日")
    docs = {a["doc_no"]: a, b["doc_no"]: b, n["doc_no"]: n}
    res = V.build(docs)
    assert res.valid_until == {}
    assert any("列优先" in u for u in res.unresolved)


def test_apply_keeps_the_later_end_and_marks_source():
    a = doc("A", "t", "x", "2031-08-15")
    b = doc("B", "t", "x", None)
    n = doc("沪人社规〔2022〕24号", "关于延长部分行政规范性文件有效期的通知",
            "沪A〔2020〕1号 | 延长至2027年12月31日")
    docs = {"A": a, "B": b, n["doc_no"]: n}
    # A 自身到期（2031）晚于延期日（2027），不能被改小
    res = V.build(docs)
    V.apply(res, docs)
    assert a["valid_until"] == "2031-08-15"
    assert a["validity_source"] == "document"
    assert b["validity_source"] == "unknown"


def test_vision_cells_merge_wrapped_fragments():
    """扫描件里一个单元格常被拆成两行：「沪人社福发」+「（2010）2号」。"""
    from tools.ingest import vision

    obs = [
        {"text": "沪人社福发", "y": 0.836, "x": 0.61, "h": 0.02, "page": 0},
        {"text": "（2010）2号", "y": 0.856, "x": 0.61, "h": 0.02, "page": 0},
        {"text": "沪人社养发", "y": 0.900, "x": 0.61, "h": 0.02, "page": 0},
        {"text": "（2016）9号", "y": 0.920, "x": 0.61, "h": 0.02, "page": 0},
    ]
    cells = vision._cells(obs, lambda t: bool(vision.RE_CELL_NO.match(t.strip())), 0.02)
    assert [c[1] for c in cells] == ["沪人社福发（2010）2号", "沪人社养发（2016）9号"]


def test_vision_needs_all_four_headers():
    """表头认不全就不猜列边界。"""
    from tools.ingest import vision

    assert vision._columns([{"text": "序号", "x": 0.1, "y": 0.1, "h": 0.02, "page": 0}]) is None
