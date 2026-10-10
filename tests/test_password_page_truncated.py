import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _load_function(name: str):
    tree = ast.parse(TEXT, filename=str(SOURCE))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


def _function_body(name: str) -> str:
    tree = ast.parse(TEXT, filename=str(SOURCE))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(TEXT, node) or ""


class PasswordStateTests(unittest.TestCase):
    def test_navigation_string_state_is_normalized_without_get_error(self):
        normalize = _load_function("_normalize_page_state")
        items = _load_function("_dict_state_items")
        state = normalize("navigation_after_script", SimpleNamespace(current_url="https://auth.openai.com/create-account/password"), list_keys=("inputs", "forms"))
        self.assertEqual(state["url"], "https://auth.openai.com/create-account/password")
        self.assertEqual(state["inputs"], [])
        self.assertEqual(state["forms"], [])
        self.assertEqual(items({"inputs": ["stale", {"type": "password"}]}, "inputs"), [{"type": "password"}])

    def test_empty_job_186_dom_is_not_a_usable_password_form(self):
        has_form = _load_function("_password_state_has_form")
        state = {
            "url": "https://auth.openai.com/create-account/password",
            "inputs": [],
            "forms": [],
            "buttons": [{"visible": True}],
            "errors": [],
        }
        self.assertFalse(has_form(state))

    def test_visible_password_input_or_form_counts(self):
        has_form = _load_function("_password_state_has_form")
        self.assertTrue(has_form({"forms": [{"action": "/create-account/password"}]}))
        self.assertTrue(has_form({"inputs": [{"type": "password", "visible": True}]}))
        self.assertFalse(has_form({"inputs": [{"type": "password", "visible": False}]}))
        self.assertFalse(has_form({"error": "navigation interrupted"}))

    def test_password_page_contract_remains(self):
        self.assertIn("def _password_page_state", TEXT)
        self.assertIn("def _is_signup_password_page", TEXT)
        self.assertIn("def _is_login_password_page", TEXT)
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("passwordless", body)
        self.assertIn("create-account/password", body)


class PasswordRecoveryTests(unittest.TestCase):
    def test_empty_shell_recovery_is_bounded_and_delayed(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("_recovery_round: int = 0", TEXT)
        self.assertIn("_recovery_round >= 1", body)
        self.assertIn("empty_form_since", body)
        self.assertIn("time.time() - empty_form_since >= 10", body)
        self.assertIn('driver.execute_script("window.location.reload()")', body)

    def test_recovered_form_reuses_password_and_rechecks_state(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("重载后已进入邮箱验证码页", body)
        self.assertIn("重载后已检测到登录态", body)
        self.assertIn("_password_value=password", body)
        self.assertIn("recovered_password or password", body)

    def test_native_resubmit_requires_a_live_password_form(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("if error_state and _password_state_has_form(error_state)", body)
        self.assertIn("密码表单已消失，跳过无效的原生补交", body)

    def test_stalled_password_form_gets_one_bounded_reload_recovery(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn('not final_state.get("errors")', body)
        self.assertIn("_password_state_has_form(final_state) and _recovery_round < 1", body)
        self.assertIn("window.location.reload()", body)
        self.assertIn("_recovery_round=_recovery_round + 1", body)
        self.assertIn("_password_value=password", body)

    def test_persistent_empty_shell_is_reported_as_truncated_navigation(self):
        body = _function_body("_fill_password_page_if_present")
        self.assertIn("密码提交后跳转被截断", body)
        self.assertIn("重载后仍停留在空密码页", body)

    def test_resubmit_relocates_live_button_and_records_submit_trace(self):
        body = _function_body("_resubmit_signup_password_form")
        for marker in (
            "live_click_then_requestSubmit",
            "__roxy_password_submit_trace",
            "pointerdown",
            "mousedown",
            "button.click()",
            "requestSubmit",
            "setTimeout",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)


if __name__ == "__main__":
    unittest.main()
