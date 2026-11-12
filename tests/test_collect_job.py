"""采集 job 的告警逻辑：只测"预期落空"，不测网络。"""
from __future__ import annotations

from tools.jobs.collect import check_alerts


def _r(name="栏目", items=10, verified=True, **kw):
    base = {"name": name, "slug": "s", "verified": verified, "items": items,
            "items_prev": 0, "digest": "a" * 16, "digest_prev": "",
            "added": 0, "revised": 0, "removed": 0, "ingested": 0, "skipped": 0,
            "new_docs": [], "error": "", "seconds": 1.0}
    base.update(kw)
    return base


def test_verified_column_with_zero_items_alerts():
    alerts = check_alerts([_r(items=0)], have_prev=True)
    assert any("0 条" in a for a in alerts)


def test_unverified_column_with_zero_items_does_not_alert():
    alerts = check_alerts([_r(items=0, verified=False), _r(name="b", items=5)], have_prev=True)
    assert alerts == []


def test_all_columns_zero_is_one_alert_not_n():
    alerts = check_alerts([_r(name="a", items=0), _r(name="b", items=0)], have_prev=True)
    assert len(alerts) == 1
    assert "所有栏目" in alerts[0]


def test_digest_same_but_count_changed_alerts():
    alerts = check_alerts([_r(items=11, items_prev=10, digest_prev="a" * 16)], have_prev=True)
    assert any("解析可能错乱" in a for a in alerts)


def test_fetch_error_always_alerts():
    alerts = check_alerts([_r(items=5, error="ConnectError: timeout")], have_prev=True)
    assert any("抓取失败" in a for a in alerts)


def test_normal_run_has_no_alerts():
    assert check_alerts([_r(items=565, items_prev=560, digest_prev="b" * 16),
                         _r(name="b", items=120)], have_prev=True) == []
