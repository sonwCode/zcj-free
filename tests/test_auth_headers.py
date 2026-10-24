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
