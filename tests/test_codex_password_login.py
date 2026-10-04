import ast
import builtins
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
TEXT = CODEX.read_text(encoding="utf-8")


def _defined_names(tree) -> set:
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


class UndefinedCallTests(unittest.TestCase):
    """回归：Job 55-58 的 Codex callback 超时源于一个未定义的函数名。"""

    def test_no_call_to_undefined_helper(self):
        tree = ast.parse(TEXT, filename=str(CODEX))
        have = _defined_names(tree)
        bad = [
            (node.lineno, node.func.id)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("_")
            and node.func.id not in have
        ]
        self.assertEqual(bad, [], f"调用了未定义的函数：{bad}")

    def test_password_typing_helper_is_defined(self):
        tree = ast.parse(TEXT, filename=str(CODEX))
        names = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_human_type_password_by_selector", names)

    def test_visible_is_imported_from_registration(self):
        self.assertIn("    _visible,\n", TEXT)


class PasswordFillBehaviourTests(unittest.TestCase):
    def _body(self, name: str) -> str:
        tree = ast.parse(TEXT, filename=str(CODEX))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(TEXT, node) or ""
        raise AssertionError(f"{name} not found")

    def test_helper_locates_input_by_selector_not_by_js_handle(self):
        """Cloak 无法把对象里的元素句柄序列化回来，必须按选择器重新定位。"""
        body = self._body("_human_type_password_by_selector")
        self.assertIn("find_elements", body)
        self.assertIn("input[type='password']", body)
        self.assertIn("_human_type_text", body)
        self.assertIn("_visible", body)

    def test_helper_raises_when_input_missing(self):
        body = self._body("_human_type_password_by_selector")
        self.assertIn("missing_password_input", body)

    def test_dead_duplicate_guard_is_gone(self):
        """重构遗留的重复判空块已删除。"""
        body = self._body("_fill_login_password_if_present")
        self.assertEqual(body.count('if not result.get("ok"):'), 1)


class SilentSwallowTests(unittest.TestCase):
    def test_email_step_exception_is_visible(self):
        """宽泛 except 不能再把真实缺陷伪装成正常分支。"""
        self.assertNotIn('logger.info("[Codex][Browser] 未检测到邮箱输入框，可能已登录或进入下一步', TEXT)
        self.assertIn("邮箱/密码步骤异常，按已进入下一步继续", TEXT)

    def test_exception_type_is_logged(self):
        self.assertIn("type(exc).__name__, str(exc)[:180]", TEXT)


if __name__ == "__main__":
    unittest.main()
