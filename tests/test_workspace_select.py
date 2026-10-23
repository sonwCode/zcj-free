# -*- coding: utf-8 -*-
"""workspace_select 的离线测试（不联网）。"""
import base64, json, sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.workspace_select import workspace_id_from_cookies


def _enc(obj):
    raw = json.dumps(obj).encode('utf-8')
    return base64.b64encode(raw).decode('ascii')


def test_from_dict_cookie():
    token = _enc({'workspaces': [{'id': 'ws-123'}]})
    assert workspace_id_from_cookies({'oai-client-auth-session': token}) == 'ws-123'


def test_from_list_cookie():
    token = _enc({'workspaces': [{'id': 'ws-abc'}]})
    cookies = [{'name': 'other', 'value': 'x'},
               {'name': 'oai-client-auth-session', 'value': token}]
    assert workspace_id_from_cookies(cookies) == 'ws-abc'


def test_missing_padding_ok():
    token = _enc({'workspaces': [{'id': 'ws-pad'}]}).rstrip('=')
    assert workspace_id_from_cookies({'oai-client-auth-session': token}) == 'ws-pad'


def test_accounts_fallback():
    token = _enc({'accounts': [{'id': 'acc-9'}]})
    assert workspace_id_from_cookies({'oai-client-auth-session': token}) == 'acc-9'


def test_garbage_returns_empty():
    assert workspace_id_from_cookies({'oai-client-auth-session': '@@@not-base64@@@'}) == ''
    assert workspace_id_from_cookies({}) == ''
