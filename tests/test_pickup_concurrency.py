import ast
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC = ROOT / "core" / "generic_api_mail_client.py"
TEXT = SRC.read_text(encoding="utf-8")


def _load(names: set[str]):
    tree = ast.parse(TEXT, filename=str(SRC))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found = [t.id for t in targets if isinstance(t, ast.Name)]
            if any(n in names for n in found):
                body.append(node)
    namespace = {"threading": threading, "logger": __import__("logging").getLogger("t")}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), namespace)
    return namespace


class PickupTransientErrorTests(unittest.TestCase):
    """回归：公开取码服务的 TLS 连接失败必须被识别为可退避的瞬时错误。

    Remail 前面挂着 Cloudflare，并发取码时握手会被重置，表现为
    SSLError: UNEXPECTED_EOF_WHILE_READING。
    """

    def test_ssl_and_connection_errors_are_transient(self):
        ns = _load({"_is_pickup_transient_error", "_PICKUP_TRANSIENT_MARKERS"})
        fn = ns["_is_pickup_transient_error"]
        self.assertTrue(fn(Exception(
            "HTTPSConnectionPool(host='remail.aishop6.com', port=443): Max retries "
            "exceeded (Caused by SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]')))"
        )))
        self.assertTrue(fn("Connection reset by peer"))
        self.assertTrue(fn("Read timed out."))
        self.assertTrue(fn("RemoteDisconnected('Remote end closed connection')"))

    def test_business_errors_are_not_transient(self):
        ns = _load({"_is_pickup_transient_error", "_PICKUP_TRANSIENT_MARKERS"})
        fn = ns["_is_pickup_transient_error"]
        self.assertFalse(fn("HTTP 200 但未提取到 6 位验证码"))
        self.assertFalse(fn("latest-code 返回邮箱不匹配"))
        self.assertFalse(fn(""))


class PickupThrottleTests(unittest.TestCase):
    """串行只防握手重叠，速率还要靠最小间隔限制。

    实测同一出口毫秒级连发成功率仅 2/12，拉开到 0.15s 以上可升到 9/12。
    """

    def test_throttle_enforces_minimum_gap(self):
        ns = _load({"_pickup_throttle", "_PICKUP_MIN_GAP_SECONDS", "_PICKUP_LAST_TS"})
        throttle = ns["_pickup_throttle"]
        sleeps = []
        ns["_stop_sleep"] = lambda s: sleeps.append(s)
        ns["_email_cfg"] = type("C", (), {"GENERIC_API_PICKUP_MIN_GAP": 0.35})()
        ns["_PICKUP_LAST_TS"][0] = 0.0
        ns["time"] = __import__("time")
        # 第一次调用：距上次已远超间隔，不应 sleep
        throttle()
        self.assertEqual(sleeps, [])
        # 紧接着再调用：必然小于间隔，应当 sleep
        throttle()
        self.assertTrue(sleeps, "连续调用必须触发节流 sleep")
        self.assertGreater(sleeps[0], 0)

    def test_throttle_can_be_disabled(self):
        ns = _load({"_pickup_throttle", "_PICKUP_MIN_GAP_SECONDS", "_PICKUP_LAST_TS"})
        throttle = ns["_pickup_throttle"]
        sleeps = []
        ns["_stop_sleep"] = lambda s: sleeps.append(s)
        ns["_email_cfg"] = type("C", (), {"GENERIC_API_PICKUP_MIN_GAP": 0})()
        throttle()
        throttle()
        self.assertEqual(sleeps, [])

    def test_fetch_applies_throttle_inside_lock(self):
        tree = ast.parse(TEXT, filename=str(SRC))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_fetch_poll_payload":
                body = ast.get_source_segment(TEXT, node) or ""
                self.assertIn("_pickup_throttle()", body)
                self.assertLess(body.index("with _PICKUP_LOCK:"), body.index("_pickup_throttle()"))
                return
        self.fail("_fetch_poll_payload 未找到")

    def test_gap_is_configurable(self):
        cfg = (ROOT / "config" / "email.py").read_text(encoding="utf-8")
        self.assertIn("GENERIC_API_PICKUP_MIN_GAP", cfg)
        self.assertIn("'GENERIC_API_PICKUP_MIN_GAP': 'float'", cfg)


class PickupSerializationTests(unittest.TestCase):
    """并发取码会被 Cloudflare 重置，必须全局串行。"""

    def test_pickup_is_guarded_by_a_lock(self):
        self.assertIn("_PICKUP_LOCK = threading.RLock()", TEXT)

    def test_fetch_holds_lock_and_closes_session(self):
        tree = ast.parse(TEXT, filename=str(SRC))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_fetch_poll_payload":
                body = ast.get_source_segment(TEXT, node) or ""
                self.assertIn("with _PICKUP_LOCK:", body)
                self.assertIn("session.close()", body)
                return
        self.fail("_fetch_poll_payload 未找到")

    def test_pickup_no_longer_disables_tls_verification(self):
        """verify=False 会让连接更容易被中间设备破坏，恢复校验。"""
        tree = ast.parse(TEXT, filename=str(SRC))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_fetch_poll_payload":
                body = ast.get_source_segment(TEXT, node) or ""
                self.assertNotIn("verify=False", body)
                return
        self.fail("_fetch_poll_payload 未找到")


class BackoffTests(unittest.TestCase):
    def test_loop_uses_exponential_backoff_on_transient_failures(self):
        self.assertIn("transient_failures", TEXT)
        self.assertIn("backoff = min(interval * (2 ** min(transient_failures, 4)), 30)", TEXT)
        self.assertIn("连续 %s 次连接类失败，退避到 %ss 后重试", TEXT)

    def test_failure_counter_resets_after_success(self):
        self.assertIn("transient_failures = 0", TEXT)


if __name__ == "__main__":
    unittest.main()
