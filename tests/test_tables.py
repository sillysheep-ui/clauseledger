"""表格：结构、表头渲染、误判过滤。"""
from __future__ import annotations

import io

import pytest

from tools.ingest import tables as T


def test_header_aware_render():
    rows = [["序号", "违法行为", "处罚依据"],
            ["1", "安排未成年工下井", "条例第二十三条"]]
    out = T.render_rows(rows)
    assert out[0] == "序号 | 违法行为 | 处罚依据"
    assert out[1] == "序号：1 | 违法行为：安排未成年工下井 | 处罚依据：条例第二十三条"


def test_no_header_keeps_plain_cells():
    rows = [["1", "第一条内容"], ["2", "第二条内容"]]
    out = T.render_rows(rows)
    assert out == ["1 | 第一条内容", "2 | 第二条内容"]


def test_plausible_rejects_single_column_text():
    rows = [["二、推进贫困人员应保尽保"], ["四、提升基金安全"], ["五、加强经办服务"]]
    assert T.plausible(rows) is False


def test_plausible_accepts_real_table():
    rows = [["序号", "文号", "有效期"], ["1", "沪人社规〔2021〕1号", "2026-12-31"]]
    assert T.plausible(rows) is True


def test_covered_by_detects_duplicate_rendering():
    row = "序号：1 | 文件名称：关于延长部分行政规范性文件有效期的通知 | 文号：沪人社规〔2023〕4号"
    assert T.covered_by("序号文件名称文号文件有效期1关于延长部分行政规范性文件有效期的通知沪人社规〔2023〕4号", row)
    assert not T.covered_by("各单位应于月底前报送材料。", row)


def test_html_table_to_grid_and_blocks():
    from tools.ingest.parse import parse_html

    html = """<html><body><div id="ivs_content">
      <p>正文开头，说明表格用途。</p>
      <table><tr><th>序号</th><th>文号</th></tr>
      <tr><td>1</td><td>沪人社规〔2021〕1号</td></tr></table>
    </div></body></html>"""
    p = parse_html(html.encode("utf-8"), "https://example.gov.cn/x")
    assert len(p.tables) == 1
    assert p.tables[0].rows[1] == ["1", "沪人社规〔2021〕1号"]
    block = [b.text for b in p.blocks if b.kind == "table"]
    assert "文号：沪人社规〔2021〕1号" in block[1]


def test_docx_table_to_grid():
    docx = pytest.importorskip("docx")
    from tools.ingest.parse import parse_docx

    d = docx.Document()
    d.add_paragraph("附件：申请信息")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "项目"
    t.cell(0, 1).text = "内容"
    t.cell(1, 0).text = "姓名"
    t.cell(1, 1).text = "张三"
    buf = io.BytesIO()
    d.save(buf)
    p = parse_docx(buf.getvalue())
    assert p.tables and p.tables[0].rows[0] == ["项目", "内容"]
    assert any("内容：张三" in b.text for b in p.blocks if b.kind == "table")
