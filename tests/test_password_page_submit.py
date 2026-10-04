import ast
import re
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    tree = ast.parse(TEXT, filename=str(SOURCE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"function not found: {name}")


class PasswordPageSubmitTests(unittest.TestCase):
    """回归：注册密码页的提交必须可靠触发，且兜底不能选错按钮。

    Job 49：密码页 Continue 自报 clicked_enabled_submit，页面却停在
    /create-account/password 两分钟不动；随后兜底的 requestSubmit 抛
    "The specified element is not a submit button"，因为它选中的是页面第一个
    type="button"（显示/隐藏密码）而不是 type="submit"。
    """

    def test_password_submit_dispatches_full_pointer_sequence(self):
        body = _function_body("_fill_password_page_if_present")
        for marker in (
            "pointerdown",
            "mousedown",
            "pointerup",
            "mouseup",
            "clicked_enabled_submit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_password_submit_pointer_order_is_correct(self):
        body = _function_body("_fill_password_page_if_present")
        order = [
            body.index("pointerdown"),
            body.index("mousedown"),
            body.index("pointerup"),
            body.index("mouseup"),
        ]
        self.assertEqual(order, sorted(order), "密码页指针事件顺序错乱")

    def test_resubmit_only_targets_real_submit_buttons(self):
        body = _function_body("_resubmit_signup_password_form")
        self.assertIn("submitButtons", body)
        self.assertIn("isSubmit", body)
        # 旧写法用 OR 语义的逗号选择器，会命中 type="button"
        self.assertNotIn(
            "'button[type=\"submit\"],input[type=\"submit\"],button'",
            body,
        )

    def test_resubmit_never_passes_non_submit_to_requestSubmit(self):
        body = _function_body("_resubmit_signup_password_form")
        self.assertIn("submitButtons[0] || null", body)


if __name__ == "__main__":
    unittest.main()
