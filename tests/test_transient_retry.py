# -*- coding: utf-8 -*-
"""瞬时错误重试的离线测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.transient_retry import is_transient_error, retry_transient


def test_detects_curl_tls_error():
    assert is_transient_error("ConnectionError: Failed to perform, curl: (35) TLS connect error")


def test_timeout_is_not_transient():
    """超时故意不重试：请求可能已送达并处理，重试 POST 会重复建号/重复发 OTP。"""
    assert is_transient_error("requests.exceptions.ReadTimeout: timed out") is False
    assert is_transient_error("curl: (28) Operation timed out") is False


def test_ban_is_not_transient():
    assert not is_transient_error("account_deactivated")


def test_invalid_state_is_not_transient():
    assert not is_transient_error("409 invalid_state")


def test_retry_then_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Failed to perform, curl: (35) TLS connect error")
        return "ok"

    out = retry_transient(flaky, attempts=3, base_delay=0, sleep=lambda s: None)
    assert out == "ok" and calls["n"] == 3


def test_no_retry_on_business_error():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise RuntimeError("account_deactivated")

    try:
        retry_transient(bad, attempts=3, base_delay=0, sleep=lambda s: None)
        assert False, "应当抛出"
    except RuntimeError as exc:
        assert "deactivated" in str(exc)
    assert calls["n"] == 1, "业务错误不该重试"
