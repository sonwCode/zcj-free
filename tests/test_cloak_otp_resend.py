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
        self.assertEqual(otp_loop.count("_click_resend_email_otp(driver, timeout=25)"), 3)

    def test_otp_loop_never_restarts_login_flow(self):
        body = _function_source("_run_cloak_registration_impl")
        otp_loop = body[body.index("current_otp = otp_code"):body.index("profile_submitted =")]
        self.assertNotIn("_restart_cloak_email_otp", otp_loop)
        self.assertNotIn("https://chatgpt.com/auth/login", otp_loop)
        self.assertNotIn("_submit_email_and_wait_next", otp_loop)

    def test_obsolete_restart_helper_is_removed(self):
        self.assertNotIn("def _restart_cloak_email_otp", TEXT)

    def test_repeated_otp_is_excluded_before_retry(self):
        """Remail 可能在 resend 后短暂返回旧邮件，旧码不能再次提交。"""
        body = _function_source("_run_cloak_registration_impl")
        otp_loop = body[body.index("current_otp = otp_code"):body.index("profile_submitted =")]
        self.assertIn("used_otps: set[str] = set()", otp_loop)
        self.assertIn("exclude_codes=used_otps", otp_loop)
        self.assertIn("current_otp in used_otps", otp_loop)
        self.assertIn("取到已提交的旧验证码", otp_loop)
        self.assertIn("max_wait=30 if used_otps else None", otp_loop)

    def test_failed_registration_skips_broken_pipe_cleanup(self):
        body = _function_source("_run_cloak_registration_impl")
        self.assertIn("hard_cleanup = True", body)
        self.assertIn("if hard_cleanup:", body)
        hard_branch = body[body.index("if hard_cleanup:"):body.index("else:", body.index("if hard_cleanup:"))]
        self.assertIn("driver.force_kill()", hard_branch)
        self.assertNotIn("traffic_tracker.stop", hard_branch)
        self.assertNotIn("data_saver.stop", hard_branch)
        self.assertNotIn("close_cloak_driver(driver)", hard_branch)

    def test_log_describes_actual_resend_action(self):
        body = _function_source("_run_cloak_registration_impl")
        self.assertIn("点击“重新发送电子邮件”后继续等待", body)
        self.assertNotIn("重新提交邮箱触发 OTP", body)


if __name__ == "__main__":
    unittest.main()
