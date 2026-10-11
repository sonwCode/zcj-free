# -*- coding: utf-8 -*-
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TARGETS = {
    ROOT / "core" / "cloakbrowser_registration.py": "_run_cloak_registration_impl",
    ROOT / "core" / "browser_use_registration.py": "run_browser_use_registration",
    ROOT / "core" / "roxy_registration.py": "run_roxy_registration",
}


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


class RegistrationPostprocessOrderTests(unittest.TestCase):
    def test_account_is_persisted_and_twofa_finishes_before_codex(self):
        for path, function_name in TARGETS.items():
            with self.subTest(path=path.name):
                function = _function(path, function_name)
                calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
                save_lines = sorted(node.lineno for node in calls if _call_name(node) == "save_account_data")
                twofa_lines = sorted(node.lineno for node in calls if _call_name(node) == "run_twofa_before_codex")
                codex_lines = sorted(
                    node.lineno
                    for node in calls
                    if _call_name(node) in {"run_codex_oauth", "run_roxy_codex_oauth"}
                )
                self.assertGreaterEqual(len(save_lines), 2)
                self.assertEqual(len(twofa_lines), 1)
                self.assertTrue(codex_lines)
                self.assertLess(save_lines[0], twofa_lines[0])
                self.assertLess(twofa_lines[0], codex_lines[0])
                self.assertLess(codex_lines[0], save_lines[-1])

    def test_both_account_saves_disable_automatic_twofa(self):
        for path, function_name in TARGETS.items():
            with self.subTest(path=path.name):
                function = _function(path, function_name)
                saves = [
                    node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and _call_name(node) == "save_account_data"
                ]
                self.assertGreaterEqual(len(saves), 2)
                for call in saves:
                    keywords = {item.arg: item.value for item in call.keywords if item.arg}
                    value = keywords.get("auto_twofa")
                    self.assertIsInstance(value, ast.Constant)
                    self.assertIs(value.value, False)

    def test_twofa_failure_branch_precedes_codex_branch(self):
        for path, function_name in TARGETS.items():
            with self.subTest(path=path.name):
                text = ast.get_source_segment(
                    path.read_text(encoding="utf-8"), _function(path, function_name)
                ) or ""
                self.assertIn("twofa_blocked", text)
                self.assertIn("2FA 未完成，跳过 Codex", text)
                self.assertLess(text.index("if twofa_blocked:"), text.index("ENABLE_CODEX_AUTO=True"))


if __name__ == "__main__":
    unittest.main()
