import ast
import json
import re
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse


SOURCE = Path(__file__).parents[1] / "core" / "roxy_codex_oauth.py"


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class CloakSeleniumDriver:
    """Minimal stand-in for the Playwright-backed Cloak driver."""

    def __init__(self, on_kill=None):
        self._on_kill = on_kill

    def force_kill(self):
        if self._on_kill is not None:
            self._on_kill()


class _Driver:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def get_log(self, name):
        self.calls.append(name)
        return self.entries


def _load_callback_helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {
        "_safe_url_for_log",
        "_is_callback_url",
        "_extract_callback_url_from_performance_log",
        "_phone_otp_outcome_accepted",
    }
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"json": json, "urlparse": urlparse, "re": re, "logger": _Logger()}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return (
        namespace["_is_callback_url"],
        namespace["_extract_callback_url_from_performance_log"],
        namespace["_phone_otp_outcome_accepted"],
    )


def _entry(method, params):
    return {
        "message": json.dumps({
            "message": {"method": method, "params": params}
        })
    }


def _load_cleanup_helper_pair():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {"_bounded_codex_browser_call", "_is_playwright_backed_driver"}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"threading": threading, "logger": _Logger()}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["_bounded_codex_browser_call"], namespace["_is_playwright_backed_driver"]


def _load_cleanup_helper():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {"_bounded_codex_browser_call", "_is_playwright_backed_driver"}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"threading": threading, "logger": _Logger()}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["_bounded_codex_browser_call"]


class RoxyCodexCallbackTests(unittest.TestCase):
    def test_browser_cleanup_call_is_bounded(self):
        bounded = _load_cleanup_helper()
        started = threading.Event()

        def stuck_call():
            started.set()
            time.sleep(2)

        begin = time.monotonic()
        self.assertFalse(bounded("fixture", stuck_call, timeout_seconds=0.05))
        elapsed = time.monotonic() - begin
        self.assertTrue(started.is_set())
        self.assertLess(elapsed, 1.5)

    def test_playwright_cleanup_runs_in_calling_thread(self):
        bounded, is_pw = _load_cleanup_helper_pair()
        self.assertTrue(is_pw(CloakSeleniumDriver()))
        self.assertFalse(is_pw(_Driver([])))
        self.assertFalse(is_pw(None))

        driver = CloakSeleniumDriver()
        caller = threading.get_ident()
        seen = {}

        def callback():
            seen["thread"] = threading.get_ident()

        self.assertTrue(bounded("fixture", callback, timeout_seconds=1.0, driver=driver))
        self.assertEqual(seen["thread"], caller)

    def test_playwright_cleanup_timeout_forces_kill(self):
        bounded, _ = _load_cleanup_helper_pair()
        killed = []

        def stuck_call():
            time.sleep(2)

        driver = CloakSeleniumDriver(on_kill=lambda: killed.append(True))
        # 回调在调用线程内阻塞，无法被抢占；看门狗只能强制回收，
        # 因此这里断言“超时即强制回收并按失败处理”，而不是墙上时钟上限。
        self.assertFalse(bounded("fixture", stuck_call, timeout_seconds=0.05, driver=driver))
        self.assertTrue(killed)

    @classmethod
    def setUpClass(cls):
        is_callback_url, extract_callback, phone_otp_accepted = _load_callback_helpers()
        cls.is_callback_url = staticmethod(is_callback_url)
        cls.extract_callback = staticmethod(extract_callback)
        cls.phone_otp_accepted = staticmethod(phone_otp_accepted)

    def test_only_explicit_phone_outcomes_are_accepted(self):
        self.assertTrue(self.phone_otp_accepted("callback"))
        self.assertTrue(self.phone_otp_accepted("left_phone_flow"))
        for outcome in ("still_code_page", "unknown", "", None):
            with self.subTest(outcome=outcome):
                self.assertFalse(self.phone_otp_accepted(outcome))

    def test_captures_request_callback_before_page_redirects(self):
        callback = "http://localhost:1455/auth/callback?code=CODE&state=STATE"
        driver = _Driver([
            _entry("Network.requestWillBeSent", {
                "request": {"url": "https://auth.openai.com/phone-verification"},
            }),
            _entry("Network.requestWillBeSent", {
                "request": {"url": callback},
            }),
        ])

        self.assertEqual(self.extract_callback(driver), callback)
        self.assertEqual(driver.calls, ["performance"])

    def test_captures_response_callback_and_rejects_internal_callback(self):
        callback = "https://127.0.0.1:1455/auth/callback?code=CODE"
        driver = _Driver([
            _entry("Network.requestWillBeSent", {
                "request": {"url": "https://chatgpt.com/api/auth/callback/openai?code=internal"},
            }),
            _entry("Network.responseReceived", {
                "response": {"url": callback},
            }),
        ])

        self.assertEqual(self.extract_callback(driver), callback)
        self.assertTrue(self.is_callback_url(callback))
        self.assertFalse(self.is_callback_url("https://chatgpt.com/api/auth/callback/openai?code=internal"))
        self.assertFalse(self.is_callback_url("http://localhost:1456/auth/callback?code=wrong-port"))
        self.assertFalse(self.is_callback_url("http://localhost:1455/other?code=wrong-path"))

    def test_ignores_malformed_performance_entries(self):
        driver = _Driver([
            {"message": "not-json"},
            {"message": json.dumps({"message": {"method": "Network.loadingFinished"}})},
            {"unexpected": True},
        ])

        self.assertEqual(self.extract_callback(driver), "")


if __name__ == "__main__":
    unittest.main()
