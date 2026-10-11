import ast
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parents[1]
CODEX_BROWSER = ROOT / "core" / "roxy_codex_oauth.py"
CODEX_CONFIG = ROOT / "config" / "codex.py"
OTP_UTILS = ROOT / "core" / "otp_utils.py"


def function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(text, node) or ""


def load_function(path: Path, name: str):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"re": re, "parse_qs": parse_qs, "urlparse": urlparse}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    return namespace[name]


class BrowserMfaCompletionTests(unittest.TestCase):
    def test_email_otp_page_is_excluded_from_mfa_detector(self):
        body = function_source(CODEX_BROWSER, "_is_mfa_challenge_page")
        self.assertIn("email-verification", body)
        self.assertIn("pageIsMfa", body)
        self.assertIn("return False", body)

    def test_detector_separates_email_otp_mfa_and_post_submit_state(self):
        detector = load_function(CODEX_BROWSER, "_is_mfa_challenge_page")

        class Driver:
            def __init__(self, url, state):
                self.current_url = url
                self.state = state
                self.calls = 0

            def execute_script(self, *_args):
                self.calls += 1
                return self.state

        email_driver = Driver("https://auth.openai.com/email-verification", None)
        self.assertFalse(detector(email_driver))
        self.assertEqual(email_driver.calls, 0)

        mfa_driver = Driver("https://auth.openai.com/mfa-challenge/factor", {"ok": True})
        self.assertTrue(detector(mfa_driver))

        transitioned = Driver(
            "https://auth.openai.com/mfa-challenge/factor",
            {"ok": False, "post_mfa_action": True},
        )
        self.assertFalse(detector(transitioned))

    def test_mfa_submission_refreshes_totp_and_requires_page_exit(self):
        body = function_source(CODEX_BROWSER, "_fill_mfa_challenge_if_present")
        self.assertIn("_account_totp_code_for_email(email)", body)
        self.assertIn("attempts < 3", body)
        self.assertIn("not _is_mfa_challenge_page(driver)", body)
        self.assertIn("return False", body)

    def test_all_mfa_entry_points_require_verified_completion(self):
        text = CODEX_BROWSER.read_text(encoding="utf-8")
        self.assertIn("def _require_mfa_completion", text)
        self.assertNotIn("_fill_mfa_challenge_if_present(driver, email, timeout=15)", text)
        self.assertGreaterEqual(text.count("_require_mfa_completion(driver, email"), 6)
        self.assertIn("callback前仍停留在MFA challenge", text)

    def test_totp_secret_normalization_handles_uri_and_spacing(self):
        normalize = load_function(OTP_UTILS, "normalize_totp_secret")
        self.assertEqual(normalize(" jbsw y3dp-ehpk 3pxp "), "JBSWY3DPEHPK3PXP")
        self.assertEqual(
            normalize("otpauth://totp/OpenAI:user%40example.com?secret=jbswy3dpehpk3pxp&issuer=OpenAI"),
            "JBSWY3DPEHPK3PXP",
        )
        self.assertEqual(normalize(""), "")

    def test_codex_is_enabled_and_required_by_default(self):
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        self.assertIn("ENABLE_CODEX_AUTO: bool = True", text)
        self.assertIn("CODEX_REQUIRED_ON_REGISTRATION: bool = True", text)


if __name__ == "__main__":
    unittest.main()
