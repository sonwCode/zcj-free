# -*- coding: utf-8 -*-
"""号码级跨进程文件锁：同一号码同一时刻只允许一个流程在用。

来源：hyhang915 的 `core/phone_binding._phone_file_lock`。

我们的 `core/phone_registry` 只记录"用过的号码"，但**取号到登记之间有窗口**：
两个并发 worker 可以先后拿到同一个号码，都通过 "未使用" 检查，
结果一个号被两个账号同时拿去验——在 OpenAI 侧必然有一个失败，
甚至把号码直接报废。

本模块用 fcntl.flock 给每个号码一把独占锁，跨进程有效：
```
with phone_lock(number):
    ... 取号 / 提交验证 / 标记已用 ...
```
非 POSIX 环境退化为线程锁，至少保证同进程串行。
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX
except Exception:  # pragma: no cover - Windows
    fcntl = None

LOCK_DIR = Path('/opt/turb-gpt-register/data/phone_locks')
_THREAD_LOCKS: dict = {}
_THREAD_GUARD = threading.Lock()


def _lock_path(number: str) -> Path:
    key = hashlib.md5(str(number or '').encode('utf-8')).hexdigest()[:16]
    return LOCK_DIR / ('phone-%s.lock' % key)


def _thread_lock(number: str) -> threading.Lock:
    key = str(number or '')
    with _THREAD_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def phone_lock(number: str):
    """独占某个号码（同进程线程 + 跨进程文件锁）。"""
    key = str(number or '').strip()
    if not key:
        yield
        return
    tlock = _thread_lock(key)
    tlock.acquire()
    handle = None
    try:
        if fcntl is not None:
            try:
                LOCK_DIR.mkdir(parents=True, exist_ok=True)
                handle = open(_lock_path(key), 'w')
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except Exception:
                handle = None
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass
        try:
            tlock.release()
        except Exception:
            pass
