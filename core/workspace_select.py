# -*- coding: utf-8 -*-
"""浏览器 + 协议混合：跳过 /choose-an-account 直接拿 OAuth code。

背景（多方印证）：
  * ZRainbow1275 的 PRD：session 直接注入 Codex OAuth 会落在登录页；
  * Haohuangsix 的实现注释更直接："旧方案复用已登录 session 会撞
    /choose-an-account 卡死（React SPA 解析不出可提交字段）"。

它们的解法是改用纯协议登录。我们保留浏览器（它能处理人机校验），
但把**最后一跳**交给协议端点：

    POST https://auth.openai.com/api/accounts/workspace/select
         {"workspace_id": "<uuid>"}
    → Location 或 JSON 里给出下一跳，最终命中 localhost:1455/auth/callback?code=...

workspace_id 来自 cookie oai-client-auth-session（base64 的 JSON，取 workspaces[0].id）。
"""
from __future__ import annotations

import json
import logging
from base64 import b64decode

logger = logging.getLogger(__name__)

SELECT_URL = 'https://auth.openai.com/api/accounts/workspace/select'
AUTH_COOKIE = 'oai-client-auth-session'
CALLBACK_HINT = 'localhost:1455/auth/callback'


def _b64_json(value: str) -> dict:
    """base64(可带 - / _ 变体、可能缺 padding) → dict。"""
    text = str(value or '').strip()
    if not text:
        return {}
    padded = text + '=' * (-len(text) % 4)
    for decoder in (b64decode, __import__('base64').urlsafe_b64decode):
        try:
            raw = decoder(padded)
            return json.loads(raw.decode('utf-8', 'ignore'))
        except Exception:
            continue
    return {}


def workspace_id_from_cookies(cookies) -> str:
    """从 cookie 容器里取 workspace id。"""
    value = ''
    if isinstance(cookies, dict):
        value = str(cookies.get(AUTH_COOKIE) or '')
    else:
        for item in list(cookies or []):
            if isinstance(item, dict) and item.get('name') == AUTH_COOKIE:
                value = str(item.get('value') or '')
                break
    data = _b64_json(value)
    for key in ('workspaces', 'accounts'):
        items = data.get(key) or []
        for entry in items:
            if isinstance(entry, dict) and entry.get('id'):
                return str(entry['id'])
    return ''


def select_workspace(cookies, workspace_id: str, timeout: float = 25.0) -> dict:
    """用浏览器里的 cookie 走协议 select，返回 {"code": ...} 或 {"next_url": ...}。"""
    import requests

    jar = {}
    if isinstance(cookies, dict):
        jar = {k: v for k, v in cookies.items() if v}
    else:
        for item in list(cookies or []):
            if isinstance(item, dict) and item.get('name'):
                jar[str(item['name'])] = str(item.get('value') or '')
    session = requests.Session()
    session.cookies.update(jar)
    session.headers.update({
        'content-type': 'application/json',
        'accept': 'application/json',
        'origin': 'https://auth.openai.com',
        'referer': 'https://auth.openai.com/sign-in-with-chatgpt/codex/consent',
        'user-agent': 'Mozilla/5.0',
    })
    try:
        resp = session.post(SELECT_URL, data=json.dumps({'workspace_id': workspace_id}),
                            timeout=timeout, allow_redirects=False)
    except Exception as exc:
        return {'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    location = resp.headers.get('location') or ''
    body = ''
    try:
        body = resp.text or ''
    except Exception:
        body = ''
    if CALLBACK_HINT in location:
        return {'code_url': location, 'status': resp.status_code}
    try:
        data = resp.json()
    except Exception:
        data = {}
    for key in ('redirect_url', 'continue_url', 'url', 'next', 'location'):
        value = (data or {}).get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and CALLBACK_HINT in value:
            return {'code_url': value, 'status': resp.status_code}
        if isinstance(value, str) and value.startswith('/'):
            return {'next_url': 'https://auth.openai.com' + value, 'status': resp.status_code}
    return {'status': resp.status_code, 'location': location[:200], 'body': body[:300]}
