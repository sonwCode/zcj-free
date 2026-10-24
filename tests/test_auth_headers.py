# -*- coding: utf-8 -*-
"""响应头生成与 401/403 封号分类测试（纯逻辑，不联网）。"""
import base64, json, sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.auth_headers import account_api_headers, account_id_from_token, client_hint_headers, navigation_headers
from core.ban_detect import BANNED, REVOKED, classify_token_failure, is_banned_response


def _jwt(payload):
    def b64(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip('=')
    return b64({'alg': 'none'}) + '.' + b64(payload) + '.' + 'sig'


def test_client_hints_present():
    h = client_hint_headers(version='146')
    assert 'sec-ch-ua' in h and '146' in h['sec-ch-ua']
    assert h['sec-ch-ua-mobile'] == '?0'


def test_version_fallback_keeps_consistency():
    h = client_hint_headers(version='999')
    assert '146' in h['sec-ch-ua']  # 不认识的版本回落到默认，避免自相矛盾


def test_navigation_headers_have_sec_fetch():
    h = navigation_headers(url='https://chatgpt.com/', mode='navigate')
    assert h['Sec-Fetch-Mode'] == 'navigate'
    assert h['Sec-Fetch-Dest'] == 'document'
    assert h['Sec-Fetch-Site'] == 'same-origin'


def test_account_id_from_token():
    tok = _jwt({'chatgpt_account_id': 'acc-123'})
    assert account_id_from_token(tok) == 'acc-123'


def test_account_id_from_nested_auth():
    tok = _jwt({'https://api.openai.com/auth': {'chatgpt_account_id': 'acc-9'}})
    assert account_id_from_token(tok) == 'acc-9'


def test_account_api_headers_complete():
    tok = _jwt({'chatgpt_account_id': 'acc-1'})
    h = account_api_headers(tok, device_id='dev-1')
    assert h['Origin'] == 'https://chatgpt.com'
    assert h['Referer'] == 'https://chatgpt.com/'
    assert h['ChatGPT-Account-ID'] == 'acc-1'
    assert h['OAI-Device-Id'] == 'dev-1'
    assert h['Authorization'].startswith('Bearer ')


def test_401_with_ban_words_is_banned():
    body = '{"error":{"message":"Your account was deactivated for violating our policies"}}'
    assert classify_token_failure(401, body)['kind'] == BANNED
    assert is_banned_response(401, body) is True


def test_401_invalidated_token_is_revoked_not_banned():
    body = '{"error":{"message":"encountered invalidated oauth token"}}'
    r = classify_token_failure(401, body)
    assert r['kind'] == REVOKED, r


def test_potential_abuse_counts_as_ban():
    body = 'We detected potential abuse on this account'
    assert classify_token_failure(403, body)['kind'] == BANNED


def test_unknown_body():
    assert classify_token_failure(500, 'oops')['kind'] == 'unknown'


def test_probe_me_sends_browser_headers():
    """/me 探活必须带 Origin/Referer/账号 id/设备 id（dango：缺这些是典型非浏览器特征）。"""
    import types
    from core import account_inspection as ai

    captured = {}

    class _R:
        status_code = 200
        text = '{}'

    def _get(url, headers=None, **kw):
        captured.update(headers or {})
        return _R()

    mod = types.ModuleType('requests')
    try:
        import requests as _real
        mod.__dict__.update({k: v for k, v in _real.__dict__.items() if not k.startswith('__')})
    except Exception:
        pass
    mod.get = _get
    saved = sys.modules.get('requests')
    sys.modules['requests'] = mod
    try:
        tok = _jwt({'chatgpt_account_id': 'acc-7'})
        kind, _ = ai._probe_me(tok, None, device_id='dev-9')
    finally:
        # 必须还原：否则后续测试模块 import requests 会拿到这个残缺假模块
        if saved is not None:
            sys.modules['requests'] = saved
        else:
            sys.modules.pop('requests', None)
    assert kind == 'alive'
    assert captured.get('Origin') == 'https://chatgpt.com'
    assert captured.get('Referer') == 'https://chatgpt.com/'
    assert captured.get('ChatGPT-Account-ID') == 'acc-7'
    assert captured.get('OAI-Device-Id') == 'dev-9'
    assert 'sec-ch-ua' in captured


def test_device_id_is_stable_per_email():
    from core.account_inspection import _device_id_for

    a = _device_id_for('user@example.com')
    b = _device_id_for('USER@example.com')
    c = _device_id_for('other@example.com')
    assert a == b and a != c
