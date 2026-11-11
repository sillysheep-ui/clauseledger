from tools.ingest.clause import split_clauses
from tools.ingest.parse import Block


def mk(lines):
    return [Block(i, "para", t) for i, t in enumerate(lines)]


def test_statute_style():
    cs = split_clauses(mk(["第一条 为了规范管理，制定本办法。", "第二条 本办法适用于本市。"]))
    assert [c.label for c in cs] == ["第一条", "第二条"]
    assert cs[0].text.startswith("第一条")
    assert "/" not in cs[0].path


def test_notice_style_nested():
    cs = split_clauses(mk(["一、总体要求", "（一）指导思想", "坚持依法办事。", "（二）主要目标", "二、重点任务"]))
    assert [c.label for c in cs] == ["一、", "（一）", "（二）", "二、"]
    assert cs[1].path == "一、/（一）"
    # 父条包含子条全文，子条只包含自己
    assert "坚持依法办事" in cs[0].text and "坚持依法办事" in cs[1].text
    assert "二、重点任务" not in cs[0].text


def test_numbered_list_not_confused_with_decimal():
    cs = split_clauses(mk(["1.落实责任", "2.加强监督", "3.5亿元的投入"]))
    assert [c.label for c in cs] == ["1.", "2."]  # 3.5 是数字不是序号


def test_offsets_point_back_to_text():
    blocks = mk(["第一条 甲。", "第二条 乙。"])
    full = blocks[0].text + "\n" + blocks[1].text
    cs = split_clauses(blocks)
    assert full[cs[0].start:cs[1].start].strip() == "第一条 甲。"


def test_chapter_stack():
    cs = split_clauses(mk(["第一章 总则", "第一条 目的。", "第二章 附则", "第二条 施行。"]))
    assert cs[1].path == "第一章/第一条"
    assert cs[3].path == "第二章/第二条"
