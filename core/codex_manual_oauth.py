# -*- coding: utf-8 -*-
"""人工 OAuth：生成授权 URL → 用户浏览器完成 → 粘贴回调换 token。

来源：basketikun/chatgpt2api 的 /api/accounts/oauth/{start,finish}。

背景：我们服务端直连 authorize 链时，OpenAI 返回
error=login_required（The Authorization Server requires End-User authentication），
这是产品级限制，绕不过。既然如此，正解是**让人来完成浏览器那一步**，
服务端只负责 PKCE 登记与 code 换 token。

本模块只做两件事：
  start(email_hint)  → {session_id, authorize_url, state}
  finish(session_id, callback_or_code) → token 三件套
PKCE verifier 落 data/manual_oauth_sessions.json（0600），10 分钟过期。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

SESSIONS = Path('/opt/turb-gpt-register/data/manual_oauth_sessions.json')
TTL_SECONDS = 600


def _load() -> dict:
    try:
        data = json.loads(SESSIONS.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        SESSIONS.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(SESSIONS)
    except Exception:
        logger.debug('[人工OAuth] 会话写入失败')


def _purge(data: dict) -> dict:
    now = time.time()
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and now - float(v.get('at') or 0) < TTL_SECONDS}


def start(email_hint: str = '', *, prompt: str = 'login') -> dict:
    """登记一次 PKCE 会话，返回可交给用户浏览器打开的 URL。"""
    from core.codex_oauth import _build_authorize_url, _generate_pkce

    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)
    url = _build_authorize_url(state, challenge, prompt=prompt)
    data = _purge(_load())
    data[state] = {
        'verifier': verifier,
        'email_hint': str(email_hint or ''),
        'at': time.time(),
    }
    _save(data)
    logger.info('[人工OAuth] 已登记会话 state=%s...', state[:8])
    return {'session_id': state, 'state': state, 'authorize_url': url,
            'expires_in': TTL_SECONDS}


def _extract_code_state(callback: str) -> tuple[str, str]:
    raw = str(callback or '').strip()
    if not raw:
        raise ValueError('回调为空')
    if raw.startswith('http'):
        query = parse_qs(urlparse(raw).query)
        code = (query.get('code') or [''])[0]
        state = (query.get('state') or [''])[0]
        if code:
            return code, state
        raise ValueError('回调 URL 里没有 code 参数')
    if re.fullmatch(r'[A-Za-z0-9._~-]{16,}', raw):
        return raw, ''
    raise ValueError('既不是回调 URL 也不是 code')


def finish(session_id: str, callback: str, *, proxy: str | None = None) -> dict:
    """用回传的 code 换 token，并写本地凭据副本。"""
    code, url_state = _extract_code_state(callback)
    data = _purge(_load())
    key = str(session_id or url_state or '').strip()
    entry = data.get(key)
    if not entry:
        raise ValueError('会话不存在或已过期（请重新生成授权链接）')
    if url_state and key and url_state != key:
        raise ValueError('state 不匹配（可能是别的会话的回调）')

    from core.browser_session import BrowserSession
    from core.codex_oauth import build_codex_storage, exchange_codex_token, _parse_id_token

    session = BrowserSession(proxy=proxy or None, fingerprint_seed=secrets.token_hex(8))
    token_resp = exchange_codex_token(session, code, str(entry.get('verifier') or ''))
    id_claims = _parse_id_token(str(token_resp.get('id_token') or ''))
    storage = build_codex_storage(token_resp, id_claims)

    data.pop(key, None)
    _save(data)

    email = str(entry.get('email_hint') or id_claims.get('email') or '')
    try:
        from core.codex_credential_store import save as save_credential

        save_credential(json.dumps(storage, ensure_ascii=False, indent=2), email)
    except Exception:
        logger.debug('[人工OAuth] 本地凭据副本写入失败')

    return {'ok': True, 'email': email, 'storage': storage}
