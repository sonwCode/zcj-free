import ast
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "core" / "codex_oauth.py"


def load_entry():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {"_codex_result", "run_codex_oauth"}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "_cfg": types.SimpleNamespace(ENABLE_CODEX_AUTO=True),
        "_is_cpa_callback_reauth_error": lambda exc: False,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


class CodexDriverSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_entry()

    def _run(self, selected, configured="protocol"):
        calls = []
        config = types.ModuleType("config")
        config.codex = types.SimpleNamespace(
            CODEX_OAUTH_DRIVER=configured,
            ENABLE_CODEX_AUTO=True,
        )
        config.roxybrowser = types.SimpleNamespace(REGISTRATION_DRIVER="protocol")
        modules = {"config": config}
        for name in ("roxy", "browser_use", "skyvern"):
            mod = types.ModuleType(f"core.{name}_codex_oauth")
            fn_name = {
                "roxy": "run_roxy_codex_oauth",
                "browser_use": "run_browser_use_codex_oauth",
                "skyvern": "run_skyvern_codex_oauth",
            }[name]
            setattr(mod, fn_name, lambda email, _name=name, **kwargs: calls.append((_name, email, kwargs)) or {"status": "success"})
            modules[f"core.{name}_codex_oauth"] = mod
        with patch.dict(sys.modules, modules):
            result = self.ns["run_codex_oauth"]("user@example.com", force=True, driver=selected)
        return result, calls

    def test_explicit_browser_use_overrides_global_roxy(self):
        result, calls = self._run("browser_use", configured="roxy")
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls[0][0], "browser_use")
        self.assertTrue(calls[0][2]["force"])

    def test_explicit_roxy_overrides_global_browser_use(self):
        result, calls = self._run("roxy", configured="browser_use")
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls[0][0], "roxy")

    def test_missing_driver_uses_global_configuration(self):
        calls = []
        config = types.ModuleType("config")
        config.codex = types.SimpleNamespace(CODEX_OAUTH_DRIVER="skyvern", ENABLE_CODEX_AUTO=True)
        config.roxybrowser = types.SimpleNamespace(REGISTRATION_DRIVER="protocol")
        mod = types.ModuleType("core.skyvern_codex_oauth")
        mod.run_skyvern_codex_oauth = lambda email, **kwargs: calls.append(kwargs) or {"status": "success"}
        with patch.dict(sys.modules, {"config": config, "core.skyvern_codex_oauth": mod}):
            result = self.ns["run_codex_oauth"]("user@example.com", force=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 1)


class CodexDriverUiContractTests(unittest.TestCase):
    def test_both_templates_expose_single_and_bulk_driver_and_send_them(self):
        root = Path(__file__).parents[1]
        for template, single_id, bulk_id in (
            ("index.html", "codexRetryDriverV2", "codexBulkDriverV2"),
            ("index_legacy.html", "codexRetryDriver", "codexBulkDriver"),
        ):
            source = (root / "webui" / "templates" / template).read_text(encoding="utf-8")
            with self.subTest(template=template):
                self.assertIn(f'id="{single_id}"', source)
                self.assertIn(f'id="{bulk_id}"', source)
                self.assertIn("/api/codex/retry'", source)
                self.assertIn("/api/codex/retry-bulk'", source)
                self.assertIn(single_id, source[source.index("/api/codex/retry'"):])
                self.assertIn(bulk_id, source[source.index("/api/codex/retry-bulk'"):])


if __name__ == "__main__":
    unittest.main()
