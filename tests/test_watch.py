from tools.watch.watch import Item, Snapshot, diff, parse_rows

HTML = """
<table><tbody>
<tr><td><a href="/a/1.html" title="关于印发A的通知">关于印发A的通知</a></td>
    <td>沪人社规〔2026〕23号</td><td class="text-center">2026-11-05</td><td>2031-12-31</td></tr>
<tr><td><a href="/a/2.html" title="关于B的通知">关于B的通知</a></td>
    <td>沪人社规〔2026〕22号</td><td>2026-11-05</td><td></td></tr>
</tbody></table>"""


def _snap(items):
    return Snapshot(column="x", fetched_at="t", pages=1, items=items)


def test_parse_rows_keeps_doc_no_and_dates():
    items = parse_rows(HTML, "https://rsj.sh.gov.cn")
    assert len(items) == 2
    assert items[0].doc_no == "沪人社规〔2026〕23号"
    assert items[0].issued_at == "2026-11-05"
    assert items[0].valid_until == "2031-12-31"
    assert items[0].url == "https://rsj.sh.gov.cn/a/1.html"
    assert items[1].valid_until == ""


def test_diff_added_revised_removed():
    a = Item("规〔2026〕1号", "旧标题", "2026-01-01", "", "u1")
    b = Item("规〔2026〕1号", "新标题", "2026-01-01", "", "u1")   # 改标题
    c = Item("规〔2026〕2号", "新增", "2026-11-05", "", "u2")
    ev = diff(_snap([a]), _snap([b, c]))
    kinds = {(e["action"], e["doc_no"]) for e in ev}
    assert ("revised", "规〔2026〕1号") in kinds
    assert ("added", "规〔2026〕2号") in kinds
    assert ev[[e["action"] for e in ev].index("revised")]["fields"] == ["title"]


def test_diff_detects_removed():
    ev = diff(_snap([Item("规〔2026〕9号", "t", "2026-01-01", "", "u")]), _snap([]))
    assert ev == [{"action": "removed", "doc_no": "规〔2026〕9号", "title": "t", "url": "u"}]


def test_digest_stable_regardless_of_order():
    i1 = Item("1", "a", "2026-01-01", "", "u1")
    i2 = Item("2", "b", "2026-01-02", "", "u2")
    assert _snap([i1, i2]).digest() == _snap([i2, i1]).digest()
