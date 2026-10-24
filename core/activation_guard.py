# -*- coding: utf-8 -*-
"""激活守卫：进程退出时释放还没结束的接码激活（避免继续扣费）。

来源：pirder/codex_me 的 `phone_verification._cleanup_all_providers` +
`_signal_handler`——它在退出/收到信号时统一清理所有 provider 的在途号码。

我们的现状：`sms_pool_worker` 有 SIGTERM 处理，但 **注册流程** 里
`acquire_number_unused` 取到的激活号不受保护。进程被 kill / 重启时，
这些号会一直挂在接码商那边计时，直到超时才释放（等于白花钱）。

用法：
    from core.activation_guard import track, untrack
    activation_id, phone = acquire(...)
    track(activation_id, provider='herosms')
    ...
    untrack(activation_id)          # 成功/已取消后解绑
进程退出（正常退出或 SIGTERM/SIGINT）时会自动 cancel 剩下的。
"""
from __future__ import annotations

import atexit
import logging
import signal
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_TRACKED: dict = {}
_INSTALLED = False


def _release_all(reason: str = '') -> None:
    with _LOCK:
        items = list(_TRACKED.items())
        _TRACKED.clear()
    if not items:
        return
    logger.warning('[激活守卫] 退出时释放 %d 个未结束的激活（%s）', len(items), reason or 'exit')
    try:
        from core import sms_provider
    except Exception:
        return
    for activation_id, meta in items:
        try:
            sms_provider.cancel(str(activation_id), background=False)
            logger.info('[激活守卫] 已取消 activation=%s provider=%s',
                        activation_id, (meta or {}).get('provider'))
        except Exception as exc:
            logger.warning('[激活守卫] 取消失败 activation=%s: %s', activation_id, str(exc)[:120])


def _install() -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _INSTALLED = True
    atexit.register(lambda: _release_all('atexit'))

    def _handler(signum, frame):
        _release_all('signal %s' % signum)
        # 恢复默认行为，让进程真的退出
        try:
            signal.signal(signum, signal.SIG_DFL)
        except Exception:
            pass
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def track(activation_id, provider: str = '', phone: str = '') -> None:
    """登记一个在途激活。"""
    key = str(activation_id or '').strip()
    if not key:
        return
    _install()
    with _LOCK:
        _TRACKED[key] = {'provider': provider, 'phone': phone}


def untrack(activation_id) -> None:
    """解绑（成功取消或号码已用完）。"""
    key = str(activation_id or '').strip()
    if not key:
        return
    with _LOCK:
        _TRACKED.pop(key, None)


def pending() -> dict:
    with _LOCK:
        return dict(_TRACKED)
