# -*- coding: utf-8 -*-
"""按数据库文件隔离的巡检进程锁；进程退出由操作系统自动释放。

来源：上游 myfanhua 的 `core/inspection_lease`。

我们的查活既能从 WebUI 手动触发、也能从定时任务触发，
两个巡检同时跑会重复发请求、互相覆盖状态。这里用 flock 保证同一时刻只有一个巡检。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except Exception:  # pragma: no cover - Windows
    fcntl = None

_LOCK_DIR = Path('/opt/turb-gpt-register/data')
_THREAD_LOCK = threading.Lock()


class InspectionLeaseBusy(RuntimeError):
    """已有巡检在进行中。"""


@contextmanager
def acquire_inspection_lease(name: str = 'account-inspection', blocking: bool = False):
    """获取巡检锁；blocking=False 时拿不到立即抛 InspectionLeaseBusy。"""
    handle = None
    try:
        if fcntl is not None:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            path = _LOCK_DIR / ('%s.lock' % name)
            handle = open(path, 'w')
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle.fileno(), flags)
            except OSError as exc:
                raise InspectionLeaseBusy('已有巡检进行中：%s' % name) from exc
        else:
            if not _THREAD_LOCK.acquire(blocking=blocking):
                raise InspectionLeaseBusy('已有巡检进行中：%s' % name)
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
        elif fcntl is None:
            try:
                _THREAD_LOCK.release()
            except Exception:
                pass
