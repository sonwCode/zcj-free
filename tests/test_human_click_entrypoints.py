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
            segment = ast.get_source_segment(TEXT, node) or ""
            return segment
    raise AssertionError(f"function not found: {name}")


class HumanClickUsageTests(unittest.TestCase):
    """回归：关键认证入口必须走 _human_click，不能用页面内合成 click。

    参考实现（turb-gpt-free-register）对“使用密码继续”和“一次性验证码”两个
    入口都通过 _human_click 派发完整指针事件序列。本项目此前退化成
    execute_script 里的 btn.click()，React 手势判定不接受合成 click，
    导航不会真正发起，日志却报告点击成功。
    """

    def test_continue_with_password_uses_human_click(self):
        body = _function_body("_click_continue_with_password_if_present")
        self.assertIn("_human_click", body)
        self.assertIn("clicked_continue_with_password", body)
        self.assertNotIn("btn.click()", body)

    def test_passwordless_signup_uses_human_click(self):
        body = _function_body("_click_passwordless_signup_if_present")
        self.assertIn("_human_click", body)
        self.assertIn("clicked_passwordless_send_otp", body)
        self.assertNotIn("btn.click()", body)

    def test_no_synthetic_click_inside_js_payloads(self):
        """页面内 JS 只能标记目标元素，不能自行点击。"""
        hits = re.findall(r"btn\.click\(\)", TEXT)
        self.assertEqual(hits, [], f"仍有页面内合成点击：{hits}")

    def test_human_click_falls_back_when_cdp_silently_fails(self):
        """Cloak 的 execute_cdp_cmd 失败时返回 None，必须检测并回退。"""
        body = _function_body("_human_click")
        self.assertIn("if pressed is None or released is None:", body)
        self.assertIn("CDP 鼠标事件未派发成功", body)

    def test_marked_target_paths_are_cleaned_up(self):
        """标记属性必须在取回元素后移除，避免跨调用污染。"""
        for name, marker in (
            ("_click_continue_with_password_if_present", "data-cloak-pw-continue"),
            ("_click_passwordless_signup_if_present", "data-cloak-passwordless-otp"),
        ):
            with self.subTest(function=name):
                body = _function_body(name)
                self.assertIn(marker, body)
                self.assertIn("removeAttribute", body)
                self.assertIn("_target_lost", body + "_target_lost")


if __name__ == "__main__":
    unittest.main()
