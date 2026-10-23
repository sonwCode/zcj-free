# -*- coding: utf-8 -*-
"""session-token cookie 分片与跨域构造的回归测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.session_cookie import (CHUNK_LIMIT, build_cookies,
                                 build_dual_domain_cookies, split_session_token)


def test_short_token_single_cookie():
    parts = split_session_token('short-token')
    assert parts == [('__Secure-next-auth.session-token', 'short-token')]


def test_long_token_split_at_3800():
    token = 'x' * (CHUNK_LIMIT + 500)
    parts = split_session_token(token)
    assert len(parts) == 2
    assert parts[0][0].endswith('.0') and parts[1][0].endswith('.1')
    assert len(parts[0][1]) == CHUNK_LIMIT
    assert ''.join(v for _, v in parts) == token


def test_very_long_token_three_chunks():
    token = 'y' * (CHUNK_LIMIT * 2 + 10)
    parts = split_session_token(token)
    assert len(parts) == 3
    assert ''.join(v for _, v in parts) == token


def test_dual_domain_shares_device_id():
    cookies = build_dual_domain_cookies('tok', account_id='acc-1', device_id='dev-9')
    domains = set()
    dids = []
    for item in cookies:
        domains.add(item['domain'])
        if item['name'] == 'oai-did':
            dids.append(item['value'])
    assert domains == {'chatgpt.com', 'auth.openai.com'}
    assert dids == ['dev-9', 'dev-9'], '两个域必须共用同一个 oai-did'


def test_build_cookies_includes_account():
    cookies = build_cookies('tok', account_id='acc-1', device_id='dev-9', domain='auth.openai.com')
    names = {item['name'] for item in cookies}
    assert '__Secure-next-auth.session-token' in names
    assert '_account' in names
    assert 'oai-did' in names
    assert all(item['domain'] == 'auth.openai.com' for item in cookies)
