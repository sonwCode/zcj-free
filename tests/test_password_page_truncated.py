import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _load(name: str):
    tree = ast.parse(TEXT, filename=str(SOURCE))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if not body:
        raise AssertionError(f"{name} not found")
    namespace: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


def _function_body(name: str) -> str:
    tree = ast.parse(TEXT, filename=str(SOURCE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"{name} not found")


class PasswordStateHasFormTests(unittest.TestCase):
    """Job 53：密码提交后跳转被截断，URL 仍在密码路由但 DOM 是空壳。"""

    def test_empty_dom_is_not_a_usable_password_page(self):
        has_form = _load("_password_state_has_form")
        job53_state = {
            "url": "https://auth.openai.com/create-account/password",
            "inputs": [],
            "forms": [],
            "buttons": [{"type": "", "name": "", "id": "", "disabled": False, "visible": True}],
            "errors": [],
        }
        self.assertFalse(has_form(job53_state))

    def test_real_password_page_is_detected(self):
        has_form = _load("_password_state_has_form")
        state = {
            "url": "https://auth.openai.com/create-account/password",
            "inputs": [{"type": "password", "name": "new-password", "autocomplete": "new-password", "visible": True}],
            "forms": [{"action": "/create-account/password"}],
            "buttons": [],
            "errors": [],
        }
        self.assertTrue(has_form(state))

    def test_form_alone_is_enough(self):
        has_form = _load("_password_state_has_form")
        self.assertTrue(has_form({"forms": [{"action": "/create-account/password"}]}))

    def test_invisible_password_input_does_not_count(self):
        has_form = _load("_password_state_has_form")
        state = {"inputs": [{"type": "password", "visible": False}], "forms": []}
        self.assertFalse(has_form(state))

    def test_error_state_is_not_treated_as_form(self):
        has_form = _load("_password_state_has_form")
        self.assertFalse(has_form({"error": "TimeoutException"}))
        self.assertFalse(has_form({}))


class PasswordRecoveryTests(unittest.TestCase):
    def test_recovery_round_parameter_is_bounded(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("_recovery_round: int = 0", TEXT)
        self.assertIn("_recovery_round < 1", body)

    def test_empty_dom_triggers_a_single_reload(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("reloaded_empty_password_page", body)
        self.assertIn("driver.refresh()", body)
        self.assertIn("密码路由下已无密码表单，整页重载后重新判定", body)

    def test_reload_rechecks_next_state(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("重载后已进入邮箱验证码页", body)
        self.assertIn("重载后已检测到登录态", body)
        self.assertIn("重载后密码表单已恢复，重新填写并提交", body)

    def test_timeout_message_distinguishes_truncated_navigation(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("密码提交后跳转被截断", body)


if __name__ == "__main__":
    unittest.main()
