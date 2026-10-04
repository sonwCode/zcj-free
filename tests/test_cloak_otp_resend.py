import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TARGET = ROOT / "core" / "cloakbrowser_registration.py"
TEXT = TARGET.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    tree = ast.parse(TEXT, filename=str(TARGET))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"{name} not found")


class CloakOtpResendTests(unittest.TestCase):
    """Cloak 注册 OTP 重试必须留在当前验证会话，不能重开登录入口。"""

    def test_both_retry_branches_click_resend(self):
        body = _function_source("_run_cloak_registration_impl")
        otp_loop = body[body.index("current_otp = otp_code"):body.index("profile_submitted =")]
        self.assertEqual(otp_loop.count("_click_resend_email_otp(driver, timeout=25)"), 2)

    def test_otp_loop_never_restarts_login_flow(self):
        body = _function_source("_run_cloak_registration_impl")
        otp_loop = body[body.index("current_otp = otp_code"):body.index("profile_submitted =")]
        self.assertNotIn("_restart_cloak_email_otp", otp_loop)
        self.assertNotIn("https://chatgpt.com/auth/login", otp_loop)
        self.assertNotIn("_submit_email_and_wait_next", otp_loop)

    def test_obsolete_restart_helper_is_removed(self):
        self.assertNotIn("def _restart_cloak_email_otp", TEXT)

    def test_same_value_from_new_mail_is_allowed(self):
        """OpenAI resend 可能发送新邮件但沿用同一个六码；只按 after_ts 判断新旧。"""
        body = _function_source("_run_cloak_registration_impl")
        otp_loop = body[body.index("current_otp = otp_code"):body.index("profile_submitted =")]
        self.assertIn("wait_for_otp(email, after_ts=otp_after_ts)", otp_loop)
        self.assertNotIn("used_otps", otp_loop)
        self.assertNotIn("exclude_codes=", otp_loop)
        self.assertNotIn("取码接口仍返回已提交的旧验证码", otp_loop)

    def test_log_describes_actual_resend_action(self):
        body = _function_source("_run_cloak_registration_impl")
        self.assertIn("点击“重新发送电子邮件”后继续等待", body)
        self.assertNotIn("重新提交邮箱触发 OTP", body)


if __name__ == "__main__":
    unittest.main()
