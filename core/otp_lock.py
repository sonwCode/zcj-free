# -*- coding: utf-8 -*-
"""按邮箱的验证码锁：同一个邮箱同一时刻只允许一个流程在取码。

来源：starmiaoa 的 `login/login_flow._login_otp_lock_for(email)`。

为什么需要：同一账号被并发处理时（重试任务 + 主任务、或两个 worker 撞号），
两个流程会各拉一封 OTP；后到的那封作废了前一封，于是先提交的流程永远验不过，
表现为"验证码一直错"，实际是我们自己把码换了。

我们已有 `phone_lock`（按号码），但没有按邮箱的锁。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_LOCKS: dict = {}
_GUARD = threading.Lock()


def _key(email: str) -> str:
    return str(email or '').strip().lower()


def _lock_for(email: str) -> threading.Lock:
    key = _key(email)
    with _GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def otp_lock(email: str, *, blocking: bool = True):
    """串行化同一邮箱的取码流程。

    blocking=False 时拿不到锁抛 OtpLockBusy（调用方可以选择跳过本轮）。
    """
    key = _key(email)
    if not key:
        yield
        return
    lock = _lock_for(key)
    acquired = lock.acquire(blocking=blocking)
    if not acquired:
        raise OtpLockBusy('该邮箱正在取码：%s' % key)
    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:
            pass


class OtpLockBusy(RuntimeError):
    """同一邮箱已有流程在取码。"""


def active_count() -> int:
    with _GUARD:
        return len(_LOCKS)
