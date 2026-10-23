# -*- coding: utf-8 -*-
"""把 Codex 授权并入协议会话（不启浏览器）。

依据第 16 轮的结论：
  * 浏览器注入 session-token 过 OAuth 会撞 /choose-an-account（多方印证）；
  * 正解是纯协议：授权 URL → 服务端认会话 → workspace/select → callback code。

我们在注册/查活时已经把会话 cookie 存进账号 extra.auth_cookies，
本模块就用它直接建一个协议会话跑完整授权，全程不需要浏览器、密码或 OTP。
"""
from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)


def _load_cookies_into_session(session, cookies) -> int:
    """把账号里存的跨域 cookie 灌进协议会话的 jar。"""
    added = 0
    jar = getattr(getattr(session, 'session', None), 'cookies', None)
    for item in list(cookies or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '')
        value = item.get('value')
        if not name or value is None:
            continue
        domain = str(item.get('domain') or '')
        path = str(item.get('path') or '/')
        try:
            jar.set(name, str(value), domain=domain or None, path=path)
            added += 1
        except Exception:
            continue
    return added


def authorize_from_session(session, *, prompt: str = 'none') -> dict:
    """在已带登录 cookie 的会话上跑 Codex 授权，返回 code + verifier。"""
    from core import codex_oauth as proto

    verifier, challenge = proto._generate_pkce()
    state = uuid.uuid4().hex
    auth_url = proto._build_authorize_url(state, challenge, prompt=prompt)
    try:
        proto._bootstrap_authorize(session, state, challenge, auth_url)
    except Exception as exc:
        return {'ok': False, 'stage': 'bootstrap',
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
    try:
        workspace_id = proto._get_workspace_id(session)
    except Exception as exc:
        return {'ok': False, 'stage': 'workspace_id',
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
    try:
        callback = proto._select_workspace_and_get_callback(session, state)
    except Exception as exc:
        return {'ok': False, 'stage': 'workspace_select',
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160]),
                'workspace_id': workspace_id}
    code = proto._extract_code(callback, state) or ''
    return {
        'ok': bool(code),
        'code': code,
        'verifier': verifier,
        'workspace_id': workspace_id,
        'callback': str(callback or '')[:200],
    }


def codex_from_account(account: dict, *, exchange: bool = True) -> dict:
    """端到端：账号 cookie → 协议授权 → 换 token。"""
    extra = account.get('extra') if isinstance(account.get('extra'), dict) else {}
    if not extra:
        try:
            raw = account.get('extra_json')
            extra = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except Exception:
            extra = {}
    cookies = extra.get('auth_cookies') or []
    if not cookies:
        return {'ok': False, 'stage': 'cookies', 'error': '账号没有保存跨域 cookie'}
    from core.session import BrowserSession

    session = BrowserSession()
    try:
        count = _load_cookies_into_session(session, cookies)
        if not count:
            return {'ok': False, 'stage': 'cookies', 'error': 'cookie 灌入失败'}
        result = authorize_from_session(session)
        if not result.get('ok'):
            result['cookies_loaded'] = count
            return result
        if not exchange:
            return result
        try:
            from core import codex_oauth as proto

            payload = proto.exchange_codex_token(session, result['code'], result['verifier'])
        except Exception as exc:
            result['exchange_error'] = '%s: %s' % (type(exc).__name__, str(exc)[:160])
            return result
        result['token_keys'] = sorted((payload or {}).keys())
        result['exchanged'] = True
        return result
    finally:
        try:
            from core.session import close_browser_session

            close_browser_session(session)
        except Exception:
            pass
