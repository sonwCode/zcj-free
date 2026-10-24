# -*- coding: utf-8 -*-
"""warmup 守卫测试（假 session，不联网）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.auth_warmup_gate import WarmupFailed, ensure_oai_did, has_oai_did


class _Cookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class _Jar:
    def __init__(self):
        self.items = []

    def __iter__(self):
        return iter(self.items)

    def get(self, name):
        for c in self.items:
            if c.name == name:
                return c.value
        return None


class _Inner:
    def __init__(self):
        self.cookies = _Jar()


class FakeSession:
    def __init__(self, seed_on_call=None):
        self.session = _Inner()
        self.calls = 0
        self.seed_on_call = seed_on_call

    def get(self, url, **kw):
        self.calls += 1
        if self.seed_on_call and self.calls >= self.seed_on_call:
            self.session.cookies.items.append(_Cookie('oai-did', 'did-value'))
        return object()


def test_detects_cookie():
    s = FakeSession()
    assert has_oai_did(s) is False
    s.session.cookies.items.append(_Cookie('oai-did', 'x'))
    assert has_oai_did(s) is True


def test_empty_cookie_value_is_not_valid():
    s = FakeSession()
    s.session.cookies.items.append(_Cookie('oai-did', '   '))
    assert has_oai_did(s) is False


def test_ensure_succeeds_after_retry():
    s = FakeSession(seed_on_call=2)
    assert ensure_oai_did(s, attempts=4, sleep=lambda x: None) is True
    assert s.calls == 2


def test_ensure_fails_exhausted():
    s = FakeSession(seed_on_call=None)
    try:
        ensure_oai_did(s, attempts=3, sleep=lambda x: None)
        assert False, '应当抛 WarmupFailed'
    except WarmupFailed as exc:
        assert 'oai-did' in str(exc)
    assert s.calls == 3


def test_rotate_called_between_attempts():
    s = FakeSession(seed_on_call=None)
    rotations = {'n': 0}

    def rotate():
        rotations['n'] += 1

    try:
        ensure_oai_did(s, attempts=3, sleep=lambda x: None, rotate_proxy=rotate)
    except WarmupFailed:
        pass
    assert rotations['n'] == 2, rotations


def test_already_have_cookie_no_request():
    s = FakeSession()
    s.session.cookies.items.append(_Cookie('oai-did', 'x'))
    assert ensure_oai_did(s, attempts=4) is True
    assert s.calls == 0
