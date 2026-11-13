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
