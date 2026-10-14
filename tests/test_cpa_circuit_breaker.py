import ast
import json
import re
import threading
import time
import types
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "core" / "codex_oauth.py"
_FUNCTIONS = {
    "_cpa_retry_after_seconds",
    "_is_cpa_transient_error",
    "_cpa_request_json",
    "_cpa_request_json_once",
}
_ASSIGNMENTS = {
    "_CPA_REQUEST_LOCK",
    "_CPA_LAST_REQUEST_AT",
    "_CPA_COOLDOWN_UNTIL",
    "_CPA_MIN_REQUEST_INTERVAL",
    "_CPA_TRANSIENT_ERROR_TOKENS",
}


class _Logger:
    def warning(self, *args, **kwargs):
        return None


class _Response:
    status_code = 403
    text = "IP banned due to failed attempts. Try again in 3m"

    def json(self):
        return {"error": self.text}


class _Session:
    def request(self, *args, **kwargs):
        return _Response()

    def close(self):
        return None


def _load():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in _FUNCTIONS | {"CpaIpBannedError"}:
            body.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if any(name in _ASSIGNMENTS for name in names):
                body.append(node)
    namespace = {
        "json": json,
        "re": re,
        "threading": threading,
        "time": time,
        "types": types,
        "logger": _Logger(),
        "_cfg": types.SimpleNamespace(CPA_REQUEST_RETRIES=1, CPA_REQUEST_RETRY_DELAY=0.01, CPA_REQUEST_TIMEOUT=1),
        "curl_requests": types.SimpleNamespace(Session=_Session),
        "_cpa_management_origin": lambda: "http://cpa.test",
        "_cpa_management_key": lambda: "fixture-key",
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)  # noqa: S102
    return namespace


class CpaCircuitBreakerTests(unittest.TestCase):
    def test_retry_after_parses_minutes_and_seconds(self):
        ns = _load()
        self.assertEqual(ns["_cpa_retry_after_seconds"]("Try again in 26m24s"), 1584)
        self.assertEqual(ns["_cpa_retry_after_seconds"]("Try again in 2h"), 7200)
        self.assertEqual(ns["_cpa_retry_after_seconds"]("no duration"), 1800)

    def test_management_403_ip_ban_is_typed(self):
        ns = _load()
        with self.assertRaises(ns["CpaIpBannedError"]) as caught:
            ns["_cpa_request_json_once"]("GET", "/v0/management/codex-auth-url")
        self.assertEqual(caught.exception.retry_after_seconds, 180)
        self.assertIn("status=403", str(caught.exception))

    def test_cooldown_blocks_following_management_requests(self):
        ns = _load()
        ns["_CPA_MIN_REQUEST_INTERVAL"] = 0
        calls = []

        def banned_once(*args, **kwargs):
            calls.append(args)
            raise ns["CpaIpBannedError"]("IP banned", 180)

        ns["_cpa_request_json_once"] = banned_once
        with self.assertRaises(ns["CpaIpBannedError"]):
            ns["_cpa_request_json"]("GET", "/v0/management/codex-auth-url")
        with self.assertRaises(ns["CpaIpBannedError"]) as caught:
            ns["_cpa_request_json"]("GET", "/v0/management/codex-auth-url")
        self.assertEqual(len(calls), 1)
        self.assertIn("cooldown active", str(caught.exception))

    def test_concurrent_requests_are_serialized(self):
        ns = _load()
        ns["_CPA_MIN_REQUEST_INTERVAL"] = 0
        state = {"active": 0, "maximum": 0, "calls": 0}
        state_lock = threading.Lock()
        start = threading.Barrier(3)

        def request_once(*args, **kwargs):
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            with state_lock:
                state["active"] -= 1
            return {"ok": True}

        ns["_cpa_request_json_once"] = request_once
        errors = []

        def run():
            try:
                start.wait()
                ns["_cpa_request_json"]("GET", "/v0/management/codex-auth-url")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(state["calls"], 2)
        self.assertEqual(state["maximum"], 1)


if __name__ == "__main__":
    unittest.main()
