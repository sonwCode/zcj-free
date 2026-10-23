# -*- coding: utf-8 -*-
"""出口边缘探测：注册前先判断这条代理能不能被 ChatGPT 边缘正常服务。

借鉴 2951461586/GPT-Register-Tool 的 proxy/edge_probe.py：
把"能连上"和"ChatGPT 愿意服务"分成两个问题。TCP/TLS 通但被 CF 403 拦的出口，
在注册里只会浪费一次完整流程（我们的 SSL_ERROR_SYSCALL / CF 挑战失败就是这么来的）。

判定词表：
  clean    边缘正常服务（200/3xx）或到达源站并返回 401/429 —— 只有这种能用来建号
  blocked  403 或返回 HTML 挑战（cf-chl / Just a moment / cf-mitigated: challenge）
  degraded 其它非明确拒绝（5xx 等），先别用
  dead     传输失败（连接/TLS/超时）

探测只做一次匿名 GET，不发邮件、不建号、不改任何状态。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CLEAN = 'clean'
BLOCKED = 'blocked'
DEGRADED = 'degraded'
DEAD = 'dead'

#: 注册入口页，Cloudflare 前置；能正常拿到它就说明这条出口可用于注册。
ENTRY_URL = 'https://chatgpt.com/auth/login'
_CF_MARKERS = ('cf-chl', 'just a moment', 'cf-mitigated', 'attention required')

# 进程内判定缓存：proxy -> (verdict, expires_at)
_VERDICTS: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 600.0


@dataclass
class EdgeVerdict:
    proxy: str
    status: str
    http_status: int | None = None
    detail: str = ''
    elapsed_ms: int = 0

    @property
    def usable(self) -> bool:
        return self.status == CLEAN


def _redact(proxy: str) -> str:
    """日志里不出现代理账号密码。"""
    text = str(proxy or '')
    if '@' in text:
        head, tail = text.split('@', 1)
        return head.split('://')[0] + '://***@' + tail
    return text


def probe_exit(proxy: str, *, timeout: float = 15.0) -> EdgeVerdict:
    """对单条出口做只读探测，永不抛异常。"""
    started = time.time()
    target = str(proxy or '').strip()
    if not target:
        return EdgeVerdict(proxy='', status=DEAD, detail='空代理（直连不参与注册池）')
    try:
        import requests

        resp = requests.get(
            ENTRY_URL,
            proxies={'http': target, 'https': target},
            timeout=timeout,
            allow_redirects=False,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                     '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
                     'Accept': 'text/html,application/xhtml+xml'},
        )
    except Exception as exc:
        return EdgeVerdict(proxy=target, status=DEAD, detail='%s: %s' % (type(exc).__name__, str(exc)[:120]),
                           elapsed_ms=int((time.time() - started) * 1000))

    code = int(getattr(resp, 'status_code', 0) or 0)
    body = (resp.text or '')[:2000].lower()
    elapsed_ms = int((time.time() - started) * 1000)
    cf_header = str(resp.headers.get('cf-mitigated') or '').lower()

    if code in (200, 204) or 300 <= code < 400 or code in (401, 429):
        verdict = CLEAN
        detail = 'static-ok' if code < 400 else 'origin-reached'
    elif code == 403 or cf_header == 'challenge' or any(m in body for m in _CF_MARKERS):
        verdict = BLOCKED
        detail = 'cloudflare-block'
    else:
        verdict = DEGRADED
        detail = 'http-%s' % code
    return EdgeVerdict(proxy=target, status=verdict, http_status=code, detail=detail, elapsed_ms=elapsed_ms)


def remember(verdict: EdgeVerdict) -> None:
    if not verdict.proxy:
        return
    _VERDICTS[verdict.proxy] = (verdict.status, time.time() + _CACHE_TTL)


def cached_status(proxy: str) -> str | None:
    item = _VERDICTS.get(str(proxy or ''))
    if not item:
        return None
    status, expires = item
    if time.time() > expires:
        _VERDICTS.pop(str(proxy or ''), None)
        return None
    return status


def mark_blocked(proxy: str, *, cooldown_seconds: float | None = None) -> None:
    """把某条出口标成 blocked，并让它进入代理池冷却。"""
    target = str(proxy or '').strip()
    if not target:
        return
    _VERDICTS[target] = (BLOCKED, time.time() + _CACHE_TTL)
    try:
        from config.proxy import note_proxy_failure

        note_proxy_failure(target, cooldown_seconds)
    except Exception:
        pass


def pick_clean_exit(candidates: list[str], *, exclude: set[str] | None = None,
                    timeout: float = 15.0, max_probe: int = 6) -> EdgeVerdict | None:
    """从候选出口里挑第一条 clean 的；每条失败即进入冷却。"""
    banned = {str(x).strip() for x in (exclude or set()) if str(x).strip()}
    probed = 0
    for proxy in candidates:
        target = str(proxy or '').strip()
        if not target or target in banned:
            continue
        cached = cached_status(target)
        if cached == CLEAN:
            return EdgeVerdict(proxy=target, status=CLEAN, detail='cached')
        if cached in (BLOCKED, DEAD, DEGRADED):
            continue
        if probed >= max_probe:
            break
        probed += 1
        verdict = probe_exit(target, timeout=timeout)
        remember(verdict)
        logger.info('[出口探测] %s → %s（%s, %sms）', _redact(target), verdict.status, verdict.detail, verdict.elapsed_ms)
        if verdict.usable:
            return verdict
        mark_blocked(target)
    return None
