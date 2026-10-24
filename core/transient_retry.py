# -*- coding: utf-8 -*-
"""瞬时网络错误识别与重试（TLS/连接级）。

来源：sky3100 的 `platforms/chatgpt/payment_protocol`：
`_is_curl_tls_transient_error` + `_execute_stage_with_tls_retry`。

我们线上真实出现过这类错误：
  ConnectionError: Failed to perform, curl: (35) ... TLS connect error
  curl_cffi.requests.exceptions.HTTPError（偶发）
它们**不是**账号问题，重试一次往往就过了；不重试则会记成"失败/已废"，
污染漏斗统计，甚至误删好号。

用法：
    from core.transient_retry import retry_transient
    resp = retry_transient(lambda: session.get(url), attempts=3, tag="2FA")
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

#: 明确的瞬时网络/TLS 关键字（小写匹配）
TRANSIENT_MARKERS = (
    "failed to perform",
    "tls connect error",
    "ssl_error",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporarily unavailable",
    "remote disconnected",
    "eof occurred",
    "recv failure",
    "send failure",
    "curl: (7)",
    "curl: (35)",
    "curl: (52)",
    "curl: (56)",
)

#: 超时类：**故意不重试**。请求可能已经送达并被服务端处理，
#: 只是响应丢了；重试 POST 会导致重复建号或重复发送 OTP，
#: 比失败一次更糟（实测结论来自 dango-register 的 148 轮扫描）。
TIMEOUT_MARKERS = (
    "timed out",
    "timeout",
    "curl: (28)",
    "read timeout",
    "timed out.",
)

#: 不该重试的业务错误（防止把封禁当网络抖动重试）
NON_TRANSIENT_MARKERS = (
    "account_deactivated",
    "account_banned",
    "invalid_grant",
    "unauthorized",
    "invalid_state",
)


def is_transient_error(error) -> bool:
    """判断异常/文本是否属于瞬时网络故障。"""
    text = str(error or "").lower()
    if not text:
        return False
    if any(marker in text for marker in NON_TRANSIENT_MARKERS):
        return False
    if any(marker in text for marker in TIMEOUT_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_MARKERS)


def retry_transient(fn: Callable, *, attempts: int = 3, base_delay: float = 1.0,
                     tag: str = "", sleep: Callable[[float], None] = time.sleep):
    """执行 fn，仅对瞬时网络错误重试。"""
    attempts = max(1, int(attempts or 1))
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if i >= attempts or not is_transient_error(exc):
                raise
            delay = base_delay * (2 ** (i - 1))
            logger.warning("[重试] %s 第 %d/%d 次失败（%s），%.1fs 后重试",
                           tag or "任务", i, attempts, type(exc).__name__, delay)
            sleep(delay)
    raise last if last else RuntimeError("retry_transient: 无异常但未返回")
