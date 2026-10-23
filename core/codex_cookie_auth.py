# -*- coding: utf-8 -*-
"""用 session-token cookie 直接授权 Codex（不需要密码/OTP/2FA）。

借鉴 ZRainbow1275/AutoTeam-F research 里的上游配方：
  注入 chatgpt.com + auth.openai.com 两域 cookie（>3800 字节分片、共用同一 oai-did）
  → 打开 Codex OAuth authorize → 监听 localhost:1455 回调拿 code → 换 token。

为什么值得做：
  现在的 Codex 补跑要么走「密码 + TOTP」，要么走「邮箱 OTP」，
  两者都依赖邮箱可取件、且会触发风控；cookie 路径只用到已保存的会话。

前置：账号 extra.session_cookies 里要有 __Secure-next-auth.session-token（含分片）。
"""
from __future__ import annotations

import logging
import re
import time
import uuid

logger = logging.getLogger(__name__)

REDIRECT_HINT = 'localhost:1455/auth/callback'
_CODE_RE = re.compile(r'[?&]code=([A-Za-z0-9._~-]+)')


def _session_cookies_of(account: dict) -> dict:
    extra = account.get('extra') if isinstance(account.get('extra'), dict) else {}
    if not extra:
        try:
            import json

            raw = account.get('extra_json')
            if isinstance(raw, str) and raw.strip():
                extra = json.loads(raw)
        except Exception:
            extra = {}
    cookies = extra.get('session_cookies') or {}
    return cookies if isinstance(cookies, dict) else {}


def _fetch_session_in_page(driver) -> dict:
    """在页面里 fetch /api/auth/session，拿 accessToken 与 account.id。"""
    script = '''
const done = arguments[arguments.length - 1];
fetch('/api/auth/session', {credentials: 'include'})
  .then(r => r.json())
  .then(j => done(j || {}))
  .catch(e => done({__error: String(e)}));
'''
    try:
        data = driver.execute_async_script(script)
    except Exception as exc:
        return {'__error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    return data if isinstance(data, dict) else {}


def authorize_with_cookies(account: dict, *, profile_name: str | None = None,
                           timeout_s: float = 120.0) -> dict:
    """完整链路：注入 cookie → OAuth authorize → 拿 code → 换 token。"""
    from config import codex as codex_cfg  # noqa: F401  (触发配置加载)
    from core import codex_oauth as proto
    from core.session_cookie import (build_cookies, inject,
                                     rebuild_token)

    email = str(account.get('email') or '')
    cookies_map = _session_cookies_of(account)
    token = rebuild_token(cookies_map)
    if not token:
        return {'ok': False, 'error': '账号没有保存 session-token cookie'}
    device_id = str(account.get('device_id') or '') or str(uuid.uuid4())

    driver = None
    try:
        from core.cloakbrowser_driver import build_cloak_driver, close_cloak_driver

        driver, _info = build_cloak_driver(
            proxy=None, profile_name=profile_name or ('codex-' + email.replace('@', '-')),
        )
        # 1) chatgpt.com 域注入后取会话
        inject(driver, build_cookies(token, device_id=device_id, domain='chatgpt.com'))
        driver.get('https://chatgpt.com/')
        time.sleep(3)
        session_data = _fetch_session_in_page(driver)
        if session_data.get('__error'):
            return {'ok': False, 'error': '读取会话失败：%s' % session_data['__error']}
        if not session_data.get('accessToken'):
            return {'ok': False, 'error': 'cookie 注入后仍未登录（accessToken 缺失）'}
        account_id = str((session_data.get('account') or {}).get('id') or '')

        # 2) auth.openai.com 域注入（同一 oai-did、带 _account）
        inject(driver, build_cookies(token, account_id=account_id,
                                     device_id=device_id, domain='auth.openai.com'))

        # 3) PKCE + authorize
        verifier, challenge = proto._generate_pkce()
        state = uuid.uuid4().hex
        auth_url = proto._build_authorize_url(state, challenge)
        driver.get(auth_url)

        # 4) 轮询回调地址
        deadline = time.time() + max(10.0, float(timeout_s))
        code = ''
        while time.time() < deadline:
            try:
                current = str(driver.current_url or '')
            except Exception:
                current = ''
            if REDIRECT_HINT in current:
                match = _CODE_RE.search(current)
                if match:
                    code = match.group(1)
                    break
            time.sleep(1.0)
        if not code:
            return {'ok': False, 'error': '未拿到 OAuth code（超时）',
                    'landing': str(getattr(driver, 'current_url', '') or '')[:200]}
    finally:
        if driver is not None:
            try:
                from core.cloakbrowser_driver import close_cloak_driver

                close_cloak_driver(driver)
            except Exception:
                pass

    # 5) 协议侧换 token
    session = None
    try:
        from core.session import BrowserSession, close_browser_session

        session = BrowserSession()
        token_payload = proto.exchange_codex_token(session, code, verifier)
    except Exception as exc:
        return {'ok': False, 'error': '换取 token 失败：%s: %s' % (type(exc).__name__, str(exc)[:160]),
                'code': code[:16]}
    finally:
        if session is not None:
            try:
                from core.session import close_browser_session as _close

                _close(session)
            except Exception:
                pass
    return {'ok': True, 'code': code[:16] + '...', 'token_payload_keys': sorted((token_payload or {}).keys())}
