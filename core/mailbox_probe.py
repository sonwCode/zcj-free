# -*- coding: utf-8 -*-
"""邮箱新颖性只读探测：判断某个地址在 OpenAI 侧是否已经注册过。

借鉴 2951461586/GPT-Register-Tool 的 registration/probe.py。

它复用注册 lane 的 ``signin -> authorize`` 握手，但**只走到 authorize 落地就停**：
不发 authorize/continue、不建密码、不发 OTP、不建号，因此不改变任何账号状态。

判定：
  /create-account[/password]        -> unregistered
  /log-in、login_password、mfa_challenge -> registered
  /email-verification 等无法判定    -> unknown（不猜）
  传输失败                          -> unknown + error

为什么重要：我们的邮箱来自 remail 池化地址，如果某个地址在此之前已被 OpenAI 见过
（甚至已被注册过），拿它建号本身就是一个低信任信号，也可能直接注册失败。
注册前先探一次，能避免白烧一轮，也能给邮箱池质量打分。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STATUS_REGISTERED = 'registered'
STATUS_UNREGISTERED = 'unregistered'
STATUS_UNKNOWN = 'unknown'

_REGISTERED_MARKERS = ('/log-in', '/login', 'login_password', 'mfa_challenge')
_UNREGISTERED_MARKERS = ('/create-account',)


def classify_landing(url: str) -> str:
    """按落地 URL 判定邮箱是否已注册。"""
    text = str(url or '').lower()
    if not text:
        return STATUS_UNKNOWN
    if any(m in text for m in _UNREGISTERED_MARKERS):
        return STATUS_UNREGISTERED
    if any(m in text for m in _REGISTERED_MARKERS):
        return STATUS_REGISTERED
    if '/email-verification' in text:
        return STATUS_UNKNOWN
    return STATUS_UNKNOWN


#: /email-verification 页上的注册臂标记（服务端正在走"注册"验证）
_SIGNUP_PAGE_MARKERS = ('passwordless', 'signup', 'create-account', 'create account')
#: 登录臂标记
_LOGIN_PAGE_MARKERS = ('welcome back', 'log in with', 'sign in to', 'enter your password')


def _classify_verification_page(html: str) -> str:
    """在 /email-verification 落地后，用页面内容区分注册臂与登录臂。"""
    text = str(html or '').lower()
    if not text:
        return STATUS_UNKNOWN
    signup = sum(1 for m in _SIGNUP_PAGE_MARKERS if m in text)
    login = sum(1 for m in _LOGIN_PAGE_MARKERS if m in text)
    if signup > login:
        return STATUS_UNREGISTERED
    if login > signup:
        return STATUS_REGISTERED
    return STATUS_UNKNOWN


def _default_proxy(exclude: list[str] | None = None) -> str | None:
    """默认走代理，并优先挑边缘干净的出口。"""
    try:
        from config.proxy import PROXY_POOL, pick_proxy_excluding

        banned = {str(x) for x in (exclude or []) if x}
        picked = pick_proxy_excluding(banned)
        if not picked:
            return None
        try:
            from core.proxy_edge_probe import CLEAN, probe_exit

            if probe_exit(picked).status != CLEAN:
                alt = pick_proxy_excluding(banned | {picked})
                if alt:
                    picked = alt
        except Exception:
            pass
        if tried is not None:
            tried.append(str(picked))
        return str(picked) or None
    except Exception:
        return None


def probe_mailbox(email: str, *, proxy: str | None = None, timeout_note: str = '',
                  attempts: int = 3) -> dict:
    """带跨出口重试的探测；任一出口给出确定结论即返回。"""
    last = None
    tried: list[str] = []
    for attempt in range(max(1, int(attempts or 1))):
        result = _probe_once(email, proxy=proxy if attempt == 0 else None, tried=tried)
        if result.get('status') != STATUS_UNKNOWN or not result.get('error'):
            if result.get('status') != STATUS_UNKNOWN:
                return result
        last = result
        if result.get('status') != STATUS_UNKNOWN:
            return result
    return last or {'status': STATUS_UNKNOWN, 'landing': '', 'error': 'no-attempt'}


def _probe_once(email: str, *, proxy: str | None = None, tried: list[str] | None = None) -> dict:
    """只读探测；返回 {status, landing, error}，永不抛异常。"""
    addr = str(email or '').strip()
    if not addr:
        return {'status': STATUS_UNKNOWN, 'landing': '', 'error': '空邮箱'}
    session = None
    try:
        from core.chatgpt_auth import get_csrf_token, signin_openai
        from core.openai_auth import follow_authorize
        from core.session import BrowserSession, close_browser_session

        session = BrowserSession(proxy=proxy if proxy is not None else _default_proxy(tried))
        csrf = get_csrf_token(session)
        authorize_url = signin_openai(session, csrf, addr)
        landing = follow_authorize(session, authorize_url)
        status = classify_landing(landing)
        page_note = ''
        if status == STATUS_UNKNOWN and '/email-verification' in str(landing):
            try:
                page = session.get(str(landing), headers=session.get_chatgpt_headers(referer='https://auth.openai.com/'))
                html = getattr(page, 'text', '') or ''
                refined = _classify_verification_page(html)
                if refined != STATUS_UNKNOWN:
                    status = refined
                    page_note = 'page-markers'
            except Exception as exc:
                page_note = 'page-fetch-failed:%s' % type(exc).__name__
        logger.info('[邮箱探测] %s → %s（落地 %s）', addr, status, str(landing)[:80])
        return {'status': status, 'landing': str(landing)[:300], 'error': '', 'note': page_note}
    except Exception as exc:
        logger.warning('[邮箱探测] %s 探测失败：%s: %s', addr, type(exc).__name__, str(exc)[:120])
        return {'status': STATUS_UNKNOWN, 'landing': '',
                'error': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
    finally:
        if session is not None:
            try:
                from core.session import close_browser_session as _close

                _close(session)
            except Exception:
                pass
