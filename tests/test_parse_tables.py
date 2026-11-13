"""解析层：表格只出现一次、单元格不重复、噪声不进正文。

这三条对应的都是"程序不报错但内容不对"的静默失败：
表格被拆成碎片、正文混进模板源码、面包屑被当成条款——都能一路污染到引用。
"""
from __future__ import annotations

import io

from tools.ingest.parse import parse_any, parse_docx, parse_html

HTML_TABLE = """<html><body>
<div id="ivs_content">
  <p>您现在的位置： 首页 / 政务公开</p>
  <p>上海市人力资源和社会保障局关于延长部分行政规范性文件有效期的通知</p>
  <p>根据《上海市行政规范性文件管理规定》，现将有关事项通知如下：</p>
  <table>
    <tr><th>序号</th><th>文件名称</th><th>文号</th></tr>
    <tr><td>1</td><td>关于印发《上海市集体合同审查办法》的通知</td><td>沪人社规〔2021〕26号</td></tr>
    <tr><td>2</td><td>关于特殊劳动关系有关问题的通知</td><td>沪劳保关发(2003)24号</td></tr>
  </table>
  <p>上海市人力资源和社会保障局</p>
  <p>2026年7月20日</p>
</div></body></html>"""


def test_table_rows_are_blocks_and_cells_are_not_repeated() -> None:
    _, p = parse_any(HTML_TABLE.encode(), "text/html", "https://example.gov.cn/a.html")
    tables = [b for b in p.blocks if b.kind == "table"]
    assert len(tables) == 3          # 表头 + 两行
    # 单元格文字只能出现在表格块里，不能再单独成段
    for word in ("序号", "文件名称", "文号"):
        assert sum(1 for b in p.blocks if b.text.strip() == word) == 0


def test_table_keeps_row_structure() -> None:
    _, p = parse_any(HTML_TABLE.encode(), "text/html", "https://example.gov.cn/a.html")
    rows = [b.text for b in p.blocks if b.kind == "table"]
    assert "序号 | 文件名称 | 文号" in rows
    assert "1 | 关于印发《上海市集体合同审查办法》的通知 | 沪人社规〔2021〕26号" in rows


def test_breadcrumb_is_dropped() -> None:
    _, p = parse_any(HTML_TABLE.encode(), "text/html", "https://example.gov.cn/a.html")
    assert not any("您现在的位置" in b.text for b in p.blocks)


def test_template_source_in_text_is_stripped() -> None:
    html = """<html><body><div id="ivs_content">
      <p>相关链接<div class="Xun-tong"><a href="/x">某链接</a></div></p>
      <p>上海市人力资源和社会保障局关于调整本市失业保险金支付标准的通知</p>
      <p>各区人力资源和社会保障局，现将有关事项通知如下：</p>
    </div></body></html>"""
    _, p = parse_any(html.encode(), "text/html", "https://example.gov.cn/a.html")
    joined = "\n".join(b.text for b in p.blocks)
    assert "class=" not in joined and "href=" not in joined
    assert "<div" not in joined


def test_docx_table_text_is_extracted() -> None:
    import docx

    d = docx.Document()
    d.add_paragraph("农民工工资保证金存款协议书")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "甲方"
    t.cell(0, 1).text = "上海市人力资源和社会保障局"
    t.cell(1, 0).text = "金额"
    t.cell(1, 1).text = "人民币壹拾万元"
    buf = io.BytesIO()
    d.save(buf)

    p = parse_docx(buf.getvalue())
    joined = "\n".join(b.text for b in p.blocks)
    # 以前这里一个字都没有：Word 表格的字不在 paragraphs 里
    assert "上海市人力资源和社会保障局" in joined
    assert "人民币壹拾万元" in joined


def test_empty_docx_reports_no_blocks_instead_of_crashing() -> None:
    import docx

    d = docx.Document()
    d.add_paragraph("　")
    buf = io.BytesIO()
    d.save(buf)
    p = parse_docx(buf.getvalue())
    assert p.blocks == []


def test_html_without_body_is_tolerated() -> None:
    p = parse_html(b"<p>hello</p>", "https://example.gov.cn/")
    assert isinstance(p.blocks, list)
