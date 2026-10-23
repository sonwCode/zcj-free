# -*- coding: utf-8 -*-
"""codex_inline 的离线测试（不联网）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.codex_inline import _load_cookies_into_session, authorize_from_session


class FakeJar:
    def __init__(self):
        self.items = []

    def set(self, name, value, domain=None, path='/'):
        self.items.append({'name': name, 'value': value, 'domain': domain, 'path': path})


class FakeCookies:
    def __init__(self):
        self.jar = FakeJar()


class FakeSession:
    def __init__(self):
        self.session = type('S', (), {})()
        self.session.cookies = FakeCookies().jar  # curl_cffi 的 cookies 本身就是 jar
        self.headers = {}

    def get(self, url, headers=None, allow_redirects=True):
        self.last = {'url': url, 'allow_redirects': allow_redirects}
        class R:
            status_code = 302
            headers = {'location': 'http://localhost:1455/auth/callback?error=login_required&state=abc'}
            text = ''
        return R()

    def get_auth_navigate_headers(self, referer='', user_initiated=False):
        return {'accept': 'text/html'}


def test_load_cookies_counts():
    s = FakeSession()
    n = _load_cookies_into_session(s, [{'name': 'a', 'value': '1', 'domain': 'auth.openai.com'},
                                       {'name': 'b', 'value': '2'},
                                       {'name': '', 'value': 'x'}])
    assert n == 2
    assert len(s.session.cookies.items) == 2


def test_load_cookies_ignores_garbage():
    s = FakeSession()
    assert _load_cookies_into_session(s, [None, 'x', {}]) == 0


def test_bootstrap_does_not_follow_redirects():
    s = FakeSession()
    result = authorize_from_session(s)
    assert s.last['allow_redirects'] is False, '必须不跟随重定向，否则会被判 localhost ban'
    assert result['ok'] is False
    assert result['stage'] == 'workspace_id'
