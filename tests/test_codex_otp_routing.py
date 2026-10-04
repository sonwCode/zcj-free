import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_codex_oauth.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _source(name):
    tree = ast.parse(TEXT, filename=str(SOURCE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(name)


class CodexOtpRoutingTests(unittest.TestCase):
    def test_password_stall_does_not_fall_back_to_passwordless_email_otp(self):
        body = _source("_fill_email_and_otp")
        marker = 'elif pw_result is None:'
        branch = body[body.index(marker):body.index('        else:\n            _maybe_click_passwordless_after_email', body.index(marker))]
        self.assertIn("_is_mfa_challenge_page(driver)", branch)
        self.assertIn("_is_email_verification_page(driver)", branch)
        self.assertIn("Codex 密码提交后未进入 MFA 或邮箱 OTP 页面", branch)
        self.assertNotIn("_maybe_click_passwordless_after_email", branch)

    def test_email_otp_and_totp_have_explicit_logs(self):
        body = _source("_fill_email_and_otp")
        self.assertIn("使用邮箱一次性验证码", body)
        self.assertIn("使用账号 TOTP", body)
        self.assertIn("_fill_mfa_challenge_if_present(driver, email", body)

    def test_mfa_challenge_uses_account_totp(self):
        body = _source("_fill_mfa_challenge_if_present")
        self.assertIn("_account_totp_code_for_email(email)", body)
        self.assertIn("codex_mfa_submit", body)


if __name__ == "__main__":
    unittest.main()
