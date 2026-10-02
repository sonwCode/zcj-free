"""Small adapters for cooperative registration cancellation."""

import time


class StopRequested(BaseException):
    """User-requested cooperative cancellation for a registration task."""


def current_job_id() -> int | None:
    """Return the managed registration job bound to this thread, if any."""
    try:
        from core.registration_service import _THREAD_CTX
    except ImportError:
        return None
    value = getattr(_THREAD_CTX, "job_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def bind_job_id(job_id: int | None) -> None:
    """Bind a parent registration job to a child worker thread."""
    if job_id is None:
        return
    try:
        from core.registration_service import _THREAD_CTX
    except ImportError:
        return
    _THREAD_CTX.job_id = int(job_id)


def clear_job_id() -> None:
    """Clear a job binding installed in a child worker thread."""
    try:
        from core.registration_service import _THREAD_CTX
        delattr(_THREAD_CTX, "job_id")
    except (ImportError, AttributeError):
        pass


def check_stop_requested() -> None:
    """Raise the active registration stop exception when one is pending."""
    try:
        from core.registration_service import check_stop_requested as _check
    except ImportError:
        return
    _check()


def is_stop_requested() -> bool:
    """Return whether the current managed registration job has a stop signal."""
    try:
        from core.registration_service import is_stop_requested as _is_requested
    except ImportError:
        return False
    return bool(_is_requested())


def sleep(seconds: float, quantum: float = 0.25) -> None:
    """Sleep without hiding a stop request; usable outside managed jobs too."""
    try:
        from core.registration_service import stop_aware_sleep
    except ImportError:
        time.sleep(max(0.0, float(seconds or 0.0)))
        return
    stop_aware_sleep(seconds, quantum=quantum)
