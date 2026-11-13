"""抓取重试策略：哪些状态码值得重试，失败信息是否带得出去。"""
import pytest

from tools.ingest.fetch import FetchError, retryable


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504, 521, 522, 524])
def test_retryable_codes(code):
    assert retryable(code)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 412, 451])
def test_deterministic_codes_are_not_retried(code):
    """403/404/412 再试一百次也一样，重试只是白等。"""
    assert not retryable(code)


def test_network_error_is_retryable():
    assert retryable(None)


def test_fetch_error_carries_status_and_attempts():
    e = FetchError("https://x/y", status=403, attempts=1, last="HTTP 403")
    assert e.status == 403 and e.attempts == 1
    assert "status=403" in str(e)


def test_should_try_browser_only_for_challenge_codes():
    from tools.ingest.browser import should_try_browser

    assert should_try_browser(412) and should_try_browser(521) and should_try_browser(403)
    assert not should_try_browser(404) and not should_try_browser(200)
    assert not should_try_browser(None)      # 网络层异常，浏览器也救不了


def test_fetch_smart_does_not_call_browser_on_404(monkeypatch):
    """404 是确定性结果：不该再起一个浏览器去试。"""
    import tools.ingest.browser as bm
    import tools.ingest.fetch as fm

    def boom(*a, **k):
        raise fm.FetchError("https://x/y", status=404, attempts=1, last="HTTP 404")

    def should_not_run(*a, **k):
        raise AssertionError("404 不应该触发浏览器")

    monkeypatch.setattr(fm, "fetch", boom)
    monkeypatch.setattr(bm, "browser_fetch", should_not_run)
    with pytest.raises(fm.FetchError) as ei:
        fm.fetch_smart("https://x/y")
    assert ei.value.stage == "httpx"


def test_fetch_smart_marks_browser_stage_when_both_fail(monkeypatch):
    import tools.ingest.browser as bm
    import tools.ingest.fetch as fm

    def boom(*a, **k):
        raise fm.FetchError("https://x/y", status=412, attempts=1, last="HTTP 412")

    def also_boom(*a, **k):
        raise RuntimeError("browser got 0 bytes")

    monkeypatch.setattr(fm, "fetch", boom)
    monkeypatch.setattr(bm, "browser_fetch", also_boom)
    with pytest.raises(fm.FetchError) as ei:
        fm.fetch_smart("https://x/y")
    assert ei.value.stage == "browser" and ei.value.status == 412
