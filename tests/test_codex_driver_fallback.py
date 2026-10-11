import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "codex_oauth.py"
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
        self.assertTrue(fn(RuntimeError("等待 Codex callback 超时，最后 URL=...")))

    def test_account_level_failure_is_not_recoverable(self):
        """换驱动也救不了账号级失败，继续尝试只会浪费邮箱和短信。"""
        ns = _extract(CODEX, {"_is_codex_driver_recoverable"})
        fn = ns["_is_codex_driver_recoverable"]
        self.assertFalse(fn(RuntimeError("账号已废（account_deactivated）")))
        self.assertFalse(fn(RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")))
        self.assertFalse(fn(RuntimeError("")))


class DriverConfigTests(unittest.TestCase):
    def test_fallback_switch_is_registered_for_env_override(self):
        source = CFG.read_text(encoding="utf-8")
        self.assertIn("CODEX_OAUTH_DRIVER_FALLBACK", source)
        self.assertIn("'CODEX_OAUTH_DRIVER_FALLBACK': 'bool'", source)
        self.assertIn("CODEX_REQUIRED_ON_REGISTRATION", source)
        self.assertIn("'CODEX_REQUIRED_ON_REGISTRATION': 'bool'", source)


if __name__ == "__main__":
    unittest.main()
