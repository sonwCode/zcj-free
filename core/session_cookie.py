# -*- coding: utf-8 -*-
"""NextAuth session-token Cookie 的构造、分片与注入。

借鉴 ZRainbow1275/AutoTeam-F 的 codex_auth._inject_auth_cookies：
它把 admin 的 `__Secure-next-auth.session-token` 注入浏览器，直接走
auth.openai.com/oauth/authorize，从而**不需要密码/OTP/2FA** 就能拿 Codex code。

三个关键细节（我们此前都没做）：
  1. **超过 3800 字节必须分片**：Chrome 单 cookie 上限约 4KB，
     分片名是 `.0` / `.1`（NextAuth 约定，服务端会自动拼回）；
  2. **两个域都要注入**：chatgpt.com 与 auth.openai.com 是不同域，
     只在 chatgpt.com 注入会让 OAuth 入口回落登录页（正是他们 PRD 里的现象）；
  3. **两个域必须共用同一个 oai-did**：设备标识不一致会被判为异常会话。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SESSION_COOKIE = '__Secure-next-auth.session-token'
CHUNK_LIMIT = 3800


def split_session_token(token: str, limit: int = CHUNK_LIMIT) -> list:
    """把超长 session-token 切成 (name, value) 列表。"""
    value = str(token or '')
    if not value:
        return []
    size = max(1, int(limit or CHUNK_LIMIT))
    if len(value) <= size:
        return [(SESSION_COOKIE, value)]
    return [('%s.%d' % (SESSION_COOKIE, index), value[index * size:(index + 1) * size])
            for index in range((len(value) + size - 1) // size)]


def build_cookies(session_token: str, account_id: str = '', device_id: str = '',
                  domain: str = 'chatgpt.com') -> list:
    """构造某域下的一组 cookie（session-token 分片 + _account + oai-did）。"""
    host = str(domain or 'chatgpt.com').strip().lstrip('.')
    cookies = []
    for name, chunk in split_session_token(session_token):
        cookies.append({
            'name': name,
            'value': chunk,
            'domain': host,
            'path': '/',
            'httpOnly': True,
            'secure': True,
            'sameSite': 'Lax',
        })
    if account_id:
        cookies.append({
            'name': '_account',
            'value': str(account_id),
            'domain': host,
            'path': '/',
            'secure': True,
            'sameSite': 'Lax',
        })
    if device_id:
        cookies.append({
            'name': 'oai-did',
            'value': str(device_id),
            'domain': host,
            'path': '/',
            'secure': True,
            'sameSite': 'Lax',
        })
    return cookies


def build_dual_domain_cookies(session_token: str, account_id: str = '',
                              device_id: str = '') -> list:
    """chatgpt.com 与 auth.openai.com 两域同时注入用；共用同一 oai-did。"""
    return (build_cookies(session_token, account_id, device_id, 'chatgpt.com')
            + build_cookies(session_token, account_id, device_id, 'auth.openai.com'))


def inject(driver, cookies: list) -> dict:
    """用 CDP 注入 cookie；返回成功/总数。"""
    payload = list(cookies or [])
    if not payload:
        return {'ok': False, 'added': 0, 'total': 0, 'error': '空 cookie 列表'}
    try:
        driver.execute_cdp_cmd('Network.setCookies', {'cookies': payload})
        logger.info('[Cookie] 已注入 %d 个 cookie', len(payload))
        return {'ok': True, 'added': len(payload), 'total': len(payload), 'error': ''}
    except Exception as exc:
        logger.warning('[Cookie] 注入失败：%s', str(exc)[:140])
        return {'ok': False, 'added': 0, 'total': len(payload),
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
