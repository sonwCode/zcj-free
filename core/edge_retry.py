# -*- coding: utf-8 -*-
"""边缘挑战（Cloudflare）自动换出口重试。

借鉴 2951461586/GPT-Register-Tool 的 registration/edge_challenge.py：
命中 CF 边缘挑战时**不要整轮判失败**，而应在同一出口池内换一条出口重来。

我们此前的行为：CloudflareChallengeError 直接冒泡 → 整个注册失败 →
邮箱被消耗、还要重新排队。实测中这类失败占相当比例，且换出口后常常立刻成功。

判定依据（任一）：
  - 异常的 error_code == 'cloudflare_challenge'
  - 消息里含 'cloudflare challenge' / 'cf-chl' / 'just a moment'
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EDGE_ERROR_CODES = ('cloudflare_challenge',)
EDGE_MARKERS = ('cloudflare challenge', 'cf-chl', 'just a moment', 'attention required')


def is_edge_challenge(exc: BaseException) -> bool:
    """判断异常是否为 CF 边缘挑战。"""
    code = str(getattr(exc, 'error_code', '') or '').strip().lower()
    if code and code in EDGE_ERROR_CODES:
        return True
    text = str(exc or '').lower()
    return any(marker in text for marker in EDGE_MARKERS)


def _pick_replacement(exclude: str | None = None) -> str | None:
    """挑一条干净的替代出口。"""
    try:
        from config.proxy import PROXY_POOL, pick_proxy_excluding

        banned = {exclude} if exclude else set()
        picked = pick_proxy_excluding(banned)
        if not picked:
            return None
        try:
            from core.proxy_edge_probe import CLEAN, pick_clean_exit

            verdict = pick_clean_exit(list(PROXY_POOL), exclude=banned, max_probe=4)
            if verdict is not None and verdict.status == CLEAN:
                return verdict.proxy
        except Exception:
            pass
        return str(picked)
    except Exception:
        return None


def run_with_edge_retry(call, kwargs: dict, *, max_attempts: int = 2,
                        on_rotate=None) -> dict:
    """执行 call(**kwargs)；命中 CF 挑战时换出口重试。

    call 必须是可调用对象（例如 run_cloak_registration），
    返回 dict 结果。换出口通过覆盖 kwargs['proxy'] 实现。
    """
    attempt = 1
    current = dict(kwargs or {})
    while True:
        try:
            return call(**current)
        except Exception as exc:
            if attempt >= max(1, int(max_attempts)) or not is_edge_challenge(exc):
                raise
            old_proxy = str(current.get('proxy') or '')
            new_proxy = _pick_replacement(old_proxy or None)
            if not new_proxy:
                logger.warning('[边缘挑战] 没有可用替代出口，放弃重试')
                raise
            logger.warning('[边缘挑战] 命中 CF 挑战（第 %s 次），换出口后重试：%s',
                           attempt, '***')
            try:
                from core.proxy_edge_probe import mark_blocked

                if old_proxy:
                    mark_blocked(old_proxy)
            except Exception:
                pass
            if on_rotate is not None:
                try:
                    on_rotate(attempt, old_proxy, new_proxy)
                except Exception:
                    pass
            current['proxy'] = new_proxy
            attempt += 1
