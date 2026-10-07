# -*- coding: utf-8 -*-
"""Shared ownership boundary for Browser Use and Skyvern sessions."""
from __future__ import annotations

import logging
import threading
import time


class CloudBrowserLease:
    def __init__(self, client, session_info, *, provider_label: str, keep_open: bool, logger: logging.Logger):
        self.client = client
        self.session_info = session_info
        self.provider_label = provider_label
        self.keep_open = bool(keep_open)
        self.logger = logger
        self.browser = None
        self.connected = False
        self._browser_closed = False
        self._browser_closing = False
        self._remote_closed = False
        self._remote_lock = threading.Lock()

    def attach_browser(self, browser) -> None:
        self.browser = browser
        self.connected = browser is not None

    def close_browser(self, *, force: bool = False, reason: str = "", timeout_seconds: float = 5.0) -> bool:
        if self._browser_closed or self.browser is None:
            return True
        if self.keep_open and self.connected and not force:
            return False
        if self._browser_closing:
            return False
        browser = self.browser
        errors: list[BaseException] = []
        timed_out = threading.Event()
        self._browser_closing = True
        suffix = f" ({reason})" if reason else ""

        def _remote_watchdog() -> None:
            if timed_out.wait(max(0.1, float(timeout_seconds or 5.0))):
                return
            self.logger.warning(
                "[%s] 关闭 CDP browser 超时%s，先停止远端 session",
                self.provider_label,
                suffix,
            )
            self.close_remote(force=True, reason=f"CDP close timeout{suffix}")

        watchdog = threading.Thread(target=_remote_watchdog, name="cloud-browser-watchdog", daemon=True)
        watchdog.start()
        try:
            # Playwright sync objects are thread-affine; close on their owning thread.
            browser.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            timed_out.set()
            watchdog.join(0.05)
            self._browser_closing = False
        if errors:
            exc = errors[0]
            self.logger.warning(
                "[%s] 关闭 CDP browser 失败%s：%s: %s",
                self.provider_label,
                suffix,
                type(exc).__name__,
                str(exc)[:180],
            )
            return False
        self.browser = None
        self._browser_closed = True
        return True

    def close_remote(self, *, force: bool = False, reason: str = "", max_attempts: int = 2) -> bool:
        session_id = str(getattr(self.session_info, "session_id", "") or "").strip()
        if not session_id or not hasattr(self.client, "close_browser_session"):
            return True
        if self.keep_open and self.connected and not force:
            return False
        with self._remote_lock:
            if self._remote_closed:
                return True
            suffix = f" ({reason})" if reason else ""
            attempts = max(1, int(max_attempts or 1))
            for attempt in range(1, attempts + 1):
                try:
                    self.client.close_browser_session(session_id)
                except Exception as exc:
                    self.logger.warning(
                        "[%s] 停止远端 browser session 失败%s：session_id=%s attempt=%s/%s %s: %s",
                        self.provider_label,
                        suffix,
                        session_id,
                        attempt,
                        attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    if attempt < attempts:
                        time.sleep(0.25 * attempt)
                    continue
                self._remote_closed = True
                return True
            return False

    def close(self, *, force: bool = False, reason: str = "") -> None:
        # Server-side stop is authoritative and usually makes CDP teardown immediate.
        self.close_remote(force=force, reason=reason)
        self.close_browser(force=force, reason=reason)