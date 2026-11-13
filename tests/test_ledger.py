"""失败台账：把多次运行的尝试按文件汇总，且不让历史失败掩盖后来成功。"""
from tools.ingest.ledger import aggregate


def test_single_success():
    t = aggregate([("20261111T100000", {"doc_no": "A", "ok": True})])
    assert t["A"].state == "ok" and t["A"].attempts == 1


def test_failed_then_succeeded_is_not_marked_unreachable():
    rows = [("20261111T100000", {"doc_no": "A", "ok": False, "http_status": 412,
                                 "error": "FetchError", "msg": "HTTP 412"}),
            ("20261111T200000", {"doc_no": "A", "ok": True})]
    a = aggregate(rows)["A"]
    assert a.state == "ok_after_retry"
    assert a.first_failed == "20261111T100000" and a.last_ok == "20261111T200000"
    assert "后来成功" in a.explain()


def test_never_succeeded_keeps_status_and_stage():
    rows = [("20261111T100000", {"doc_no": "B", "ok": False, "http_status": 521,
                                 "error": "FetchError", "stage": "browser", "msg": "HTTP 521"})]
    b = aggregate(rows)["B"]
    assert b.state == "never_ok" and b.last_status == 521 and b.last_stage == "browser"
    assert "521" in b.explain()


def test_attempts_accumulate_across_runs():
    rows = [("20261111T100000", {"doc_no": "C", "ok": False}),
            ("20261111T110000", {"doc_no": "C", "ok": False}),
            ("20261111T120000", {"doc_no": "C", "ok": False})]
    assert aggregate(rows)["C"].attempts == 3
