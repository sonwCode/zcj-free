import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_codex_oauth.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    tree = ast.parse(TEXT, filename=str(SOURCE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"{name} not found")


class SmsChannelSelectionTests(unittest.TestCase):
    """Job 54：手机验证 10 次全失败，页面始终回 Phone number required。"""

    def test_channel_radio_uses_full_pointer_sequence(self):
        body = _function_body("_select_sms_channel_or_raise")
        for marker in ("pointerdown", "mousedown", "pointerup", "mouseup"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_channel_selection_reports_checked_state(self):
        body = _function_body("_select_sms_channel_or_raise")
        self.assertIn("checked: !!sms.checked", body)
        self.assertIn("SMS 通道未被勾选", body)

    def test_channel_selection_does_not_silently_continue(self):
        """选不中通道时必须显式失败，而不是继续提交空号码。"""
        body = _function_body("_select_sms_channel_or_raise")
        self.assertNotIn("if selected:", body)
        self.assertIn("raise RuntimeError", body)


class PhoneValueSurvivesChannelTests(unittest.TestCase):
    def test_phone_is_reverified_after_channel_selection(self):
        body = _function_body("_fill_phone_and_wait_for_code") if "def _fill_phone_and_wait_for_code" in TEXT else TEXT
        # 定位包含手机验证主循环的函数
        tree = ast.parse(TEXT, filename=str(SOURCE))
        target = ""
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                seg = ast.get_source_segment(TEXT, node) or ""
                if "_select_sms_channel_or_raise" in seg and "_click_add_phone_continue_button" in seg:
                    target = seg
                    break
        self.assertTrue(target, "未找到手机验证主循环")
        channel_at = target.index("_select_sms_channel_or_raise")
        refill_at = target.index("选择短信通道后手机号已丢失", channel_at)
        submit_at = target.index("_click_add_phone_continue_button", channel_at)
        self.assertLess(channel_at, refill_at, "重填必须发生在选通道之后")
        self.assertLess(refill_at, submit_at, "重填必须发生在提交之前")

    def test_refill_reuses_existing_helpers(self):
        tree = ast.parse(TEXT, filename=str(SOURCE))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                seg = ast.get_source_segment(TEXT, node) or ""
                if "选择短信通道后手机号已丢失" in seg:
                    self.assertIn("_set_phone_value(driver, e164", seg)
                    self.assertIn("_verify_add_phone_value_before_submit", seg)
                    return
        self.fail("未找到重填逻辑")


if __name__ == "__main__":
    unittest.main()
