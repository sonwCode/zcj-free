import ast
import re
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    tree = ast.parse(TEXT, filename=SOURCE)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"function not found: {name}")


class PointerSequenceTests(unittest.TestCase):
    """回归：关键认证入口必须派发完整指针事件序列。

    裸 btn.click() 只产生合成 click，Chromium 不据此触发导航，React 手势
    判定也不认；函数却会报告点击成功，形成“日志说点了、页面没反应”的静默
    失败。参考实现通过 CDP 派发完整序列，这里在页面内补齐等价序列。
    """

    def test_continue_with_password_dispatches_full_pointer_sequence(self):
        body = _function_body("_click_continue_with_password_if_present")
        for marker in (
            "pointerdown",
            "mousedown",
            "pointerup",
            "mouseup",
            "btn.click()",
            "clicked_continue_with_password",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_passwordless_signup_dispatches_full_pointer_sequence(self):
        body = _function_body("_click_passwordless_signup_if_present")
        for marker in (
            "pointerdown",
            "mousedown",
            "pointerup",
            "mouseup",
            "btn.click()",
            "clicked_passwordless_send_otp",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_pointerdown_precedes_click(self):
        """顺序必须是 pointerdown → mousedown → … → click。"""
        for name in (
            "_click_continue_with_password_if_present",
            "_click_passwordless_signup_if_present",
        ):
            with self.subTest(function=name):
                body = _function_body(name)
                order = [
                    body.index("pointerdown"),
                    body.index("mousedown"),
                    body.index("pointerup"),
                    body.index("mouseup"),
                    body.rindex("btn.click()"),
                ]
                self.assertEqual(order, sorted(order), "指针事件顺序错乱")

    def test_no_element_handle_is_returned_from_js(self):
        """不能把 DOM 元素塞进返回值：Cloak 的 json_value() 无法序列化。"""
        for name in (
            "_click_continue_with_password_if_present",
            "_click_passwordless_signup_if_present",
        ):
            with self.subTest(function=name):
                body = _function_body(name)
                self.assertNotIn("button: btn", body)
                self.assertNotIn(":\n          button", body)

    def test_failure_reason_is_not_overwritten(self):
        """失败分支不得用 **result 展开把 reason 覆盖回去。"""
        self.assertNotIn('**{k: v for k, v in result.items()', TEXT)
        self.assertNotIn("continue_with_password_target_lost", TEXT)
        self.assertNotIn("passwordless_button_target_lost", TEXT)

    def test_human_click_falls_back_when_cdp_silently_fails(self):
        """Cloak 的 execute_cdp_cmd 失败时返回 None，必须检测并回退。"""
        body = _function_body("_human_click")
        self.assertIn("if pressed is None or released is None:", body)
        self.assertIn("CDP 鼠标事件未派发成功", body)


if __name__ == "__main__":
    unittest.main()
