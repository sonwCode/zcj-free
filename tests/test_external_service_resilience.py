import ast
import json
import logging
import time
import unittest
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).parents[1]
SMS = ROOT / "core" / "sms_provider.py"
CODEX = ROOT / "core" / "codex_oauth.py"
CFG = ROOT / "config" / "codex.py"


def _extract(path: Path, names: set[str]):
    """提取指定函数与模块级常量，避免导入带外部依赖的模块。"""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(t in names for t in targets):
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    # 补上源码模块级的常用导入，使提取出的函数可以直接执行。
    import config.codex as _codex_cfg

    namespace: dict = {
        "Decimal": Decimal,
        "InvalidOperation": InvalidOperation,
        "logger": logging.getLogger("test"),
        "time": time,
        "json": json,
        "_cfg": _codex_cfg,
        "SmsProviderConfigurationError": type("SmsProviderConfigurationError", (RuntimeError,), {}),
    }
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102
    return namespace


class ExchangeRateFallbackTests(unittest.TestCase):
    """回归：汇率源失效时回退到最近已知汇率，而不是停摆或钉死常数。

    api.frankfurter.app 已返回 403/301，旧实现直接抛错掐断取号；
    而 7.2 这个陈旧常数与实际汇率（约 6.70）偏离 7% 以上。
    """

    def test_fx_failure_no_longer_aborts_number_purchase(self):
        source = SMS.read_text(encoding="utf-8")
        self.assertNotIn("停止新增号码采购", source)
        self.assertNotIn("实时人民币兑美元汇率不可用", source)

    def test_cascade_lists_more_than_one_source(self):
        ns = _extract(SMS, {"_fx_rate_sources"})
        fn = ns["_fx_rate_sources"]
        import config.codex as cfg

        original_urls = getattr(cfg, "SMS_FX_RATE_URLS", "")
        original_explicit = getattr(cfg, "SMS_FX_RATE_URL", "")
        try:
            cfg.SMS_FX_RATE_URL = ""
            urls = fn()
            self.assertGreaterEqual(len(urls), 2, "必须配置多个级联汇率源")
            cfg.SMS_FX_RATE_URL = "https://example.invalid/fx"
            self.assertEqual(fn(), ["https://example.invalid/fx"])
        finally:
            cfg.SMS_FX_RATE_URLS = original_urls
            cfg.SMS_FX_RATE_URL = original_explicit

    def test_payload_parser_handles_multiple_shapes(self):
        ns = _extract(SMS, {"_parse_usd_rate_from_payload"})
        parse = ns["_parse_usd_rate_from_payload"]
        from decimal import Decimal

        self.assertEqual(parse({"rates": {"USD": 0.149}}), Decimal("0.149"))
        self.assertEqual(parse({"conversion_rates": {"USD": 0.15}}), Decimal("0.15"))
        self.assertEqual(parse({"data": {"USD": 0.148}}), Decimal("0.148"))
        self.assertEqual(parse({"USD": 0.147}), Decimal("0.147"))
        for bad in ({}, {"rates": {}}, {"rates": {"USD": 0}}, {"rates": {"USD": "x"}}, None, "text"):
            with self.subTest(payload=bad):
                self.assertIsNone(parse(bad))

    def test_fallback_prefers_last_known_rate_over_stale_constant(self):
        ns = _extract(SMS, {"_fallback_usd_cny_rate"})
        fn = ns["_fallback_usd_cny_rate"]
        import config.codex as cfg

        original_known = getattr(cfg, "SMS_LAST_KNOWN_USD_CNY_RATE", "")
        original_configured = getattr(cfg, "SMSBOWER_USD_CNY_RATE", "")
        try:
            cfg.SMS_LAST_KNOWN_USD_CNY_RATE = "6.70"
            cfg.SMSBOWER_USD_CNY_RATE = "7.2"
            rate, source = fn()
            self.assertIn("last known", source)
            self.assertAlmostEqual(float(rate), 1 / 6.70, places=6)
        finally:
            cfg.SMS_LAST_KNOWN_USD_CNY_RATE = original_known
            cfg.SMSBOWER_USD_CNY_RATE = original_configured


class CpaRetryTests(unittest.TestCase):
    """回归：CPA 管理接口的 TLS 瞬时失败必须重试。

    Job 52 因 cpa.feixueapi.xyz TLS 握手被中断导致整轮 Codex 授权失败，
    而该接口先前没有任何重试。
    """

    def test_classifier_matches_tls_handshake_failure(self):
        ns = _extract(CODEX, {"_is_cpa_transient_error", "_CPA_TRANSIENT_ERROR_TOKENS"})
        is_transient = ns["_is_cpa_transient_error"]
        self.assertTrue(is_transient(Exception(
            "SSLError: BoringSSL SSL_connect: Connection closed abruptly (SSL_ERROR_SYSCALL)"
        )))
        self.assertTrue(is_transient(Exception("Connection reset by peer")))
        self.assertTrue(is_transient(Exception("operation timed out")))

    def test_classifier_rejects_business_error(self):
        ns = _extract(CODEX, {"_is_cpa_transient_error", "_CPA_TRANSIENT_ERROR_TOKENS"})
        is_transient = ns["_is_cpa_transient_error"]
        self.assertFalse(is_transient(Exception("管理接口失败 status=401: unauthorized")))

    def test_request_helper_retries_transient_failures(self):
        source = CODEX.read_text(encoding="utf-8")
        self.assertIn("def _cpa_request_json_once(", source)
        self.assertIn("_is_cpa_transient_error(exc)", source)

    def test_retry_config_defaults_registered(self):
        source = CFG.read_text(encoding="utf-8")
        self.assertIn("'CPA_REQUEST_RETRIES': 'int'", source)
        self.assertIn("'CPA_REQUEST_RETRY_DELAY': 'int'", source)


if __name__ == "__main__":
    unittest.main()
