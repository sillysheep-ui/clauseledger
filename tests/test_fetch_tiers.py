"""三档升级的判定逻辑：httpx → Chromium → 真 Chrome。

这些测试不打网络，只测"选哪一档"和"什么算成功"。
真实案例（2026-11-13）：成都 412/0 字 → Chromium 0 字 → 真 Chrome 5,030 汉字。
"""
import pytest

from tools.ingest import browser, cdp
from tools.ingest import fetch as fetch_mod
from tools.ingest.fetch import Fetched, FetchError, fetch_smart


def _ok(via, n=500):
    return Fetched("http://x", 200, b"x" * n, "text/html", "sha", None, 1, via=via)


class Boom(FetchError):
    pass


def _fail(status, stage="httpx"):
    return FetchError("http://x", status=status, attempts=1, last="boom", stage=stage)


def test_challenge_status_triggers_browser():
    assert browser.should_try_browser(412)
    assert browser.should_try_browser(521)
    assert not browser.should_try_browser(404)      # 没有就是没有
    assert not browser.should_try_browser(None)     # 网络异常，换浏览器也没用


def test_substantive_counts_chinese_not_bytes():
    shell = "<html><script>var a=1;</script>" + "x" * 7000 + "</html>"
    assert not browser.substantive(shell, 200)       # 7 KB 但 0 个汉字
    real = "<p>" + "上海市人力资源和社会保障局关于调整失业保险金标准的通知" * 10 + "</p>"
    assert browser.substantive(real, 200)


def test_httpx_success_does_not_launch_browser(monkeypatch):
    monkeypatch.setattr(fetch_mod, "fetch", lambda url, **kw: _ok("httpx"))
    monkeypatch.setattr(browser, "browser_fetch", lambda url, **kw: pytest.fail("不该起浏览器"))
    assert fetch_smart("http://x").via == "httpx"


def test_falls_through_to_real_chrome(monkeypatch):
    def bad(url, **kw):
        raise _fail(412)

    monkeypatch.setattr(fetch_mod, "fetch", bad)
    monkeypatch.setattr(browser, "browser_fetch",
                        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("nav loop")))
    monkeypatch.setattr(cdp, "real_chrome_fetch",
                        lambda url, **kw: (200, b"y" * 900, "text/html", "sha", None))
    f = fetch_smart("http://x")
    assert f.via == "chrome" and len(f.content) == 900


def test_all_tiers_fail_reports_every_stage(monkeypatch):
    monkeypatch.setattr(fetch_mod, "fetch", lambda url, **kw: (_ for _ in ()).throw(_fail(412)))
    monkeypatch.setattr(browser, "browser_fetch",
                        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("loop")))
    monkeypatch.setattr(cdp, "real_chrome_fetch",
                        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(FetchError) as ei:
        fetch_smart("http://x")
    msg = str(ei.value)
    assert "httpx=412" in msg and "browser=" in msg and "chrome=" in msg


def test_chrome_can_be_disabled(monkeypatch):
    monkeypatch.setattr(fetch_mod, "fetch", lambda url, **kw: (_ for _ in ()).throw(_fail(412)))
    monkeypatch.setattr(browser, "browser_fetch",
                        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("loop")))
    monkeypatch.setattr(cdp, "real_chrome_fetch", lambda url, **kw: pytest.fail("不该起真 Chrome"))
    with pytest.raises(FetchError):
        fetch_smart("http://x", allow_chrome=False)


def test_non_challenge_status_skips_browsers(monkeypatch):
    monkeypatch.setattr(fetch_mod, "fetch", lambda url, **kw: (_ for _ in ()).throw(_fail(404)))
    monkeypatch.setattr(browser, "browser_fetch", lambda url, **kw: pytest.fail("404 不该起浏览器"))
    with pytest.raises(FetchError) as ei:
        fetch_smart("http://x")
    assert ei.value.status == 404
