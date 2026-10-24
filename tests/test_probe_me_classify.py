# -*- coding: utf-8 -*-
"""/me 探活分类的回归测试（用假 requests，不联网）。"""
import sys
import types
sys.path.insert(0, '/opt/turb-gpt-register')

import config as cfg
cfg.reload_all()

from core import account_inspection as ai


class _Resp:
    def __init__(self, code, text):
        self.status_code = code
        self.text = text


_ORIGINAL_REQUESTS = sys.modules.get('requests')


_REAL_REQUESTS = None
try:
    import requests as _REAL_REQUESTS  # noqa: F401
except Exception:
    _REAL_REQUESTS = None


def _patch(code, text):
    """安装假 requests：保留真实模块的属性，只替换 get。

    直接塞一个空 ModuleType 会污染 sys.modules，
    导致后续测试模块 import requests 时 AttributeError。
    """
    mod = types.ModuleType('requests')
    if _REAL_REQUESTS is not None:
        mod.__dict__.update({k: v for k, v in _REAL_REQUESTS.__dict__.items()
                             if not k.startswith('__')})
    mod.get = lambda *a, **k: _Resp(code, text)
    sys.modules['requests'] = mod


def pytest_sessionfinish_hook():
    pass


def test_alive_on_200():
    _patch(200, '{"id":"x"}')
    assert ai._probe_me('t', None)[0] == 'alive'


def test_revoked_on_401():
    _patch(401, '{"error":"token_revoked"}')
    assert ai._probe_me('t', None)[0] == 'revoked'


def test_revoked_on_structured_ban():
    _patch(403, '{"error":{"code":"account_deactivated"}}')
    kind, detail = ai._probe_me('t', None)
    assert kind == 'revoked' and 'ban:' in detail


def test_cloudflare_403_is_unknown_not_dead():
    # 关键：CF 拦截页不能把账号判成已废
    _patch(403, '<html>Just a moment...</html>')
    assert ai._probe_me('t', None)[0] == 'unknown'


def test_server_error_is_unknown():
    _patch(500, 'oops')
    assert ai._probe_me('t', None)[0] == 'unknown'


def test_tls_transient_error_is_retried():
    """TLS 瞬时错误应重试一次，而不是直接把账号记成 unknown。"""
    import types
    calls = {"n": 0}

    class _R:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    def _get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Failed to perform, curl: (35) TLS connect error")
        return _R(200, '{"id":"x"}')

    mod = types.ModuleType("requests")
    mod.get = _get
    sys.modules["requests"] = mod
    kind, _detail = ai._probe_me("t", None)
    assert kind == "alive", kind
    assert calls["n"] == 2, "应当重试一次"
