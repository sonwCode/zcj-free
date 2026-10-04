import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
REG = ROOT / "core" / "roxy_registration.py"


def source(path, name):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(text, node) or ""


class ReferenceAuthContractTests(unittest.TestCase):
    def test_reference_codex_auth_flow_order(self):
        body = source(CODEX, "_fill_email_and_otp")
        self.assertLess(body.index("_submit_email_step(driver)"), body.index("_fill_login_password_if_present(driver, email, timeout=18)"))
        self.assertIn("_maybe_click_passwordless_after_email", body)
        self.assertIn("_wait_for_fresh_email_otp", body)

    def test_reference_password_helper_and_mfa_exist(self):
        password = source(CODEX, "_fill_login_password_if_present")
        mfa = source(CODEX, "_fill_mfa_challenge_if_present")
        self.assertIn("_account_password_for_email(email)", password)
        self.assertIn("_account_totp_code_for_email(email)", mfa)
        self.assertIn("codex_password_submit", password)
        self.assertIn("codex_mfa_submit", mfa)

    def test_registration_password_handoff_is_preserved(self):
        text = CODEX.read_text(encoding="utf-8")
        self.assertIn("_REGISTRATION_PASSWORD_CACHE", text)
        self.assertIn("db.get_pool_password(email)", text)
        self.assertIn("remember_registration_password", text)

    def test_registration_and_sms_modules_remain_present(self):
        self.assertTrue(REG.exists())
        self.assertTrue((ROOT / "core" / "sms_provider.py").exists())


if __name__ == "__main__":
    unittest.main()
