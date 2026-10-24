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


def _patch(code, text):
    mod = types.ModuleType('requests')
    mod.get = lambda *a, **k: _Resp(code, text)
    sys.modules['requests'] = mod


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
