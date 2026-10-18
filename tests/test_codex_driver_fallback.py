import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "codex_oauth.py"
CLOAK = ROOT / "core" / "cloakbrowser_registration.py"
ROXY_CODEX = ROOT / "core" / "roxy_codex_oauth.py"
CLOAK_CFG = ROOT / "config" / "cloakbrowser.py"
CFG = ROOT / "config" / "codex.py"


def _extract(path: Path, names: set[str]):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names_found = []
            for target in targets:
                if isinstance(target, ast.Name):
                    names_found.append(target.id)
                elif isinstance(target, ast.Tuple):
                    names_found.extend(e.id for e in target.elts if isinstance(e, ast.Name))
            if any(n in names or n.startswith("_CODEX_DRIVER") for n in names_found):
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    import logging
    namespace = {"logger": logging.getLogger("test")}
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102
    return namespace


class DriverChainResolutionTests(unittest.TestCase):
    """回归：Codex 驱动必须支持降级链，Roxy 额度耗尽时可自动换驱动。

    Job 补跑时 Roxy 返回“窗口额度不足”，旧实现直接整轮失败，
    即便本机还有 Cloak / Browser Use / 协议驱动可用。
    """

    def test_comma_separated_drivers_are_split_in_order(self):
        ns = _extract(CODEX, {"_resolve_oauth_drivers", "_normalize_driver_name"})
        resolve = ns["_resolve_oauth_drivers"]
        import config.codex as cfg

        original = getattr(cfg, "CODEX_OAUTH_DRIVER", "")
        original_fb = getattr(cfg, "CODEX_OAUTH_DRIVER_FALLBACK", True)
        try:
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = True
            cfg.CODEX_OAUTH_DRIVER = "cloak,browser_use,roxy"
            chain = resolve(None)
            self.assertEqual(chain[:3], ["cloak", "browser_use", "roxy"])
            # protocol 作为不依赖外部资源的兜底追加在末尾
            self.assertEqual(chain[-1], "protocol")
        finally:
            cfg.CODEX_OAUTH_DRIVER = original
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = original_fb

    def test_aliases_normalize_to_canonical_names(self):
        ns = _extract(CODEX, {"_resolve_oauth_drivers", "_normalize_driver_name"})
        resolve = ns["_resolve_oauth_drivers"]
        import config.codex as cfg

        original = getattr(cfg, "CODEX_OAUTH_DRIVER", "")
        original_fb = getattr(cfg, "CODEX_OAUTH_DRIVER_FALLBACK", True)
        try:
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = True
            cfg.CODEX_OAUTH_DRIVER = "browser,roxybrowser,sv"
            chain = resolve(None)
            self.assertEqual(chain[:2], ["roxy", "skyvern"])
        finally:
            cfg.CODEX_OAUTH_DRIVER = original
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = original_fb

    def test_duplicates_are_removed(self):
        ns = _extract(CODEX, {"_resolve_oauth_drivers", "_normalize_driver_name"})
        resolve = ns["_resolve_oauth_drivers"]
        import config.codex as cfg

        original = getattr(cfg, "CODEX_OAUTH_DRIVER", "")
        original_fb = getattr(cfg, "CODEX_OAUTH_DRIVER_FALLBACK", True)
        try:
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = True
            cfg.CODEX_OAUTH_DRIVER = "roxy,roxy,roxybrowser,cloak,protocol"
            chain = resolve(None)
            self.assertEqual(chain, ["roxy", "cloak", "protocol"])
        finally:
            cfg.CODEX_OAUTH_DRIVER = original
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = original_fb

    def test_fallback_disabled_keeps_single_driver(self):
        ns = _extract(CODEX, {"_resolve_oauth_drivers", "_normalize_driver_name"})
        resolve = ns["_resolve_oauth_drivers"]
        import config.codex as cfg

        original = getattr(cfg, "CODEX_OAUTH_DRIVER", "")
        original_fb = getattr(cfg, "CODEX_OAUTH_DRIVER_FALLBACK", True)
        try:
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = False
            cfg.CODEX_OAUTH_DRIVER = "cloak,roxy"
            self.assertEqual(resolve(None), ["cloak"])
        finally:
            cfg.CODEX_OAUTH_DRIVER = original
            cfg.CODEX_OAUTH_DRIVER_FALLBACK = original_fb

    def test_explicit_driver_argument_wins(self):
        ns = _extract(CODEX, {"_resolve_oauth_drivers", "_normalize_driver_name"})
        resolve = ns["_resolve_oauth_drivers"]
        chain = resolve("cloak")
        self.assertEqual(chain[0], "cloak")


class DriverErrorClassificationTests(unittest.TestCase):
    """只有资源/配额/依赖类错误才降级，账号级失败应当直接返回。"""

    def test_quota_error_is_recoverable(self):
        ns = _extract(CODEX, {"_is_codex_driver_recoverable"})
        fn = ns["_is_codex_driver_recoverable"]
        self.assertTrue(fn(RuntimeError("Roxy API 返回失败 POST /browser/create: 窗口额度不足")))
        self.assertTrue(fn(ImportError("No module named 'selenium'")))
        self.assertTrue(fn(RuntimeError("Roxy API 返回失败: connection reset")))

    def test_mfa_and_callback_failures_are_recoverable(self):
        ns = _extract(CODEX, {"_is_codex_driver_recoverable"})
        fn = ns["_is_codex_driver_recoverable"]
        self.assertTrue(fn(RuntimeError("codex_mfa_step_stalled: MFA challenge 提交后仍未完成")))
        self.assertTrue(fn(RuntimeError("codex_browser_cleanup_timeout: 清理超时")))
        self.assertTrue(fn(RuntimeError("等待 Codex callback 超时，最后 URL=...")))

    def test_phone_fraud_guard_and_cloudflare_stop_driver_fallback(self):
        ns = _extract(CODEX, {
            "_is_codex_driver_recoverable", "_is_codex_fraud_guard_error",
            "_is_codex_circuit_open_error", "_is_codex_cloudflare_challenge",
        })
        recoverable = ns["_is_codex_driver_recoverable"]
        self.assertFalse(recoverable(RuntimeError("fraud_guard")))
        self.assertFalse(recoverable(RuntimeError("IP banned")))
        self.assertFalse(recoverable(RuntimeError("browser_session_circuit_open")))
        self.assertFalse(recoverable(RuntimeError("Cloudflare challenge: Just a moment...")))
        self.assertTrue(ns["_is_codex_fraud_guard_error"]("suspicious behavior from phone"))
        self.assertTrue(ns["_is_codex_circuit_open_error"]("当前 BrowserSession 已熔断冷却"))
        self.assertTrue(ns["_is_codex_cloudflare_challenge"]("Just a moment..."))
        self.assertTrue(ns["_is_codex_cloudflare_challenge"]("cloudflare_challenge"))
        self.assertFalse(ns["_is_codex_fraud_guard_error"]("account_deactivated"))

    def test_roxy_phone_loop_stops_on_session_circuit_before_retry(self):
        source = ROXY_CODEX.read_text(encoding="utf-8")
        start = source.index("def _do_phone_verification_if_present")
        end = source.index("def _finish_consent_workspace", start)
        phone_flow = source[start:end]
        self.assertIn("_is_codex_circuit_open_error", phone_flow)
        self.assertIn("BrowserSessionCircuitOpenError", phone_flow)
        self.assertIn("category=failure_category", phone_flow)
        self.assertLess(phone_flow.index("if is_session_circuit"), phone_flow.index("if attempt < max_retries"))

    def test_cloak_registration_does_not_fallback_after_phone_fraud(self):
        source = CLOAK.read_text(encoding="utf-8")
        self.assertNotIn("_pick_next_codex_proxy", source)
        self.assertNotIn("suspicious behavior from phone", source)
        self.assertIn("proxy=account_proxy", source)

    def test_cloak_bootstrap_has_bounded_proxy_retry(self):
        source = CLOAK.read_text(encoding="utf-8")
        config = CLOAK_CFG.read_text(encoding="utf-8")
        self.assertIn("CLOAK_PROXY_BOOTSTRAP_RETRIES", source)
        self.assertIn("_is_cloak_proxy_bootstrap_error", source)
        self.assertIn("_CloakProxyBootstrapError", source)
        self.assertIn("proxy_bootstrap_exhausted", source)
        self.assertIn("CLOAK_DIRECT_FALLBACK_ON_PROXY_FAILURE", source)
        self.assertIn('proxy=""', source)
        self.assertIn("_run_in_isolated_thread", source)
        self.assertIn("excluded_proxies", source)
        self.assertIn("CLOAK_PROXY_BOOTSTRAP_RETRIES: int = 5", config)
        self.assertIn("CLOAK_DIRECT_FALLBACK_ON_PROXY_FAILURE: bool = True", config)

    def test_email_verified_follows_about_you_continue_url(self):
        source = (ROOT / "core" / "roxy_registration.py").read_text(encoding="utf-8")
        self.assertIn("auth.openai.com/about-you", source)
        self.assertIn("跟随验证响应的 about-you 继续地址", source)

    def test_chatgpt_fingerprint_warmup_is_optional_before_auth(self):
        source = CODEX.read_text(encoding="utf-8")
        self.assertIn("继续 Auth document 预检", source)
        self.assertIn("ChatGPT 辅助端点失败", source)
        self.assertIn("匿名初始化失败", source)

    def test_proxy_bootstrap_failures_are_classified_for_rotation(self):
        ns = _extract(CODEX, {"_is_codex_proxy_bootstrap_error", "_CODEX_PROXY_BOOTSTRAP_ERROR_MARKERS"})
        fn = ns["_is_codex_proxy_bootstrap_error"]
        self.assertTrue(fn(RuntimeError("ChatGPT fingerprint document preheat status=403")))
        self.assertTrue(fn(RuntimeError("Page.goto: net::ERR_TIMED_OUT")))
        self.assertTrue(fn(RuntimeError("curl: (56) Recv failure: Connection reset by peer")))

    def test_account_level_failure_is_not_recoverable(self):
        """换驱动也救不了账号级失败，继续尝试只会浪费邮箱和短信。"""
        ns = _extract(CODEX, {"_is_codex_driver_recoverable"})
        fn = ns["_is_codex_driver_recoverable"]
        self.assertFalse(fn(RuntimeError("账号已废（account_deactivated）")))
        self.assertFalse(fn(RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")))
        self.assertFalse(fn(RuntimeError("")))


class ProxyLeaseTests(unittest.TestCase):
    def test_excluded_proxy_is_not_selected_when_alternative_exists(self):
        import config.proxy as proxy_cfg

        original_pool = list(proxy_cfg.PROXY_POOL)
        original_leases = dict(proxy_cfg._PROXY_LEASES)
        try:
            proxy_cfg.PROXY_POOL = ["http://proxy-a", "http://proxy-b"]
            proxy_cfg._PROXY_LEASES.clear()
            selected = proxy_cfg.acquire_proxy_lease(["http://proxy-a"])
            self.assertEqual(selected, "http://proxy-b")
            proxy_cfg.release_proxy_lease(selected)
        finally:
            proxy_cfg.PROXY_POOL = original_pool
            proxy_cfg._PROXY_LEASES.clear()
            proxy_cfg._PROXY_LEASES.update(original_leases)


class DriverConfigTests(unittest.TestCase):
    def test_fallback_switch_is_registered_for_env_override(self):
        source = CFG.read_text(encoding="utf-8")
        self.assertIn("CODEX_OAUTH_DRIVER_FALLBACK", source)
        self.assertIn("'CODEX_OAUTH_DRIVER_FALLBACK': 'bool'", source)
        self.assertIn("CODEX_REQUIRED_ON_REGISTRATION", source)
        self.assertIn("'CODEX_REQUIRED_ON_REGISTRATION': 'bool'", source)
        self.assertIn("CODEX_PROXY_RETRY_ATTEMPTS: int = 2", source)
        self.assertIn("'CODEX_PROXY_RETRY_ATTEMPTS': 'int'", source)


if __name__ == "__main__":
    unittest.main()
