# -*- coding: utf-8 -*-
"""warmup 守卫：确认会话真的拿到了 `oai-did` 再往下走。

来源：dango-register 的 `CHANGES_vs_github.md`（第 53 节）。

它的实测结论：
  * 原实现"只看有没有抛异常"，而 CF 挑战 403 不会抛 → 误判成功
    （实测 3 轮 return True 但根本没有 cookie）；
  * 唯一可信信号是 **cookie jar 里有没有 `oai-did`**；
  * 拿不到 oai-did 就继续走 `authorize/continue`，**必然 409**（实测 5/5）；
  * 种 cookie 失败率 19%，成功轮耗时 3.4~10.9s，所以 timeout 要给到 40s、
    并且最多重试 4 次（每次换出口 IP，不换指纹）；
  * **失败要拦在 `create_mailbox` 之前** —— 邮箱是花钱的。

本模块只做"检查 + 有界重试"，不改变现有流程；由配置开关控制是否启用。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

COOKIE_NAME = 'oai-did'
DEFAULT_WARMUP_URL = 'https://chatgpt.com/'


class WarmupFailed(RuntimeError):
    """warmup 未拿到 oai-did，继续走只会 409。"""


def has_oai_did(session) -> bool:
    """从会话的 cookie jar 里判断 oai-did 是否存在。"""
    try:
        jar = getattr(getattr(session, 'session', None), 'cookies', None)
        if jar is not None:
            try:
                for cookie in jar:
                    if str(getattr(cookie, 'name', '')).lower() == COOKIE_NAME:
                        if str(getattr(cookie, 'value', '') or '').strip():
                            return True
            except TypeError:
                pass
            try:
                value = jar.get(COOKIE_NAME)
                if value is not None and str(value).strip():
                    return True
            except Exception:
                pass
    except Exception:
        pass
    # 退化：会话自己记录了 device_id 且显式声明已种 cookie
    checker = getattr(session, '_has_cookie_for_domain', None)
    if callable(checker):
        for domain in ('.chatgpt.com', 'chatgpt.com', '.openai.com'):
            try:
                if checker(COOKIE_NAME, domain):
                    return True
            except Exception:
                continue
    return False


def ensure_oai_did(session, *, attempts: int = 4, timeout: float = 40.0,
                   warmup_url: str = DEFAULT_WARMUP_URL,
                   rotate_proxy=None, sleep=time.sleep) -> bool:
    """确保会话拿到 oai-did；失败抛 WarmupFailed。

    rotate_proxy: 可选回调，每轮失败后调用一次（换出口 IP，**不换指纹**）。
    """
    attempts = max(1, int(attempts or 1))
    for i in range(1, attempts + 1):
        if has_oai_did(session):
            logger.info('[warmup] 已有 %s（第 %d 轮直接通过）', COOKIE_NAME, i)
            return True
        try:
            session.get(warmup_url, timeout=timeout, allow_redirects=True)
        except Exception as exc:
            logger.warning('[warmup] 第 %d/%d 轮请求失败：%s: %s',
                           i, attempts, type(exc).__name__, str(exc)[:120])
        if has_oai_did(session):
            logger.info('[warmup] 第 %d/%d 轮拿到 %s', i, attempts, COOKIE_NAME)
            return True
        logger.warning('[warmup] 第 %d/%d 轮未拿到 %s，准备换出口重试', i, attempts, COOKIE_NAME)
        if rotate_proxy and i < attempts:
            try:
                rotate_proxy()
            except Exception:
                pass
        if i < attempts:
            sleep(min(3.0, 0.5 * i))
    raise WarmupFailed('warmup 未拿到 %s（已重试 %d 轮），继续只会 409' % (COOKIE_NAME, attempts))
