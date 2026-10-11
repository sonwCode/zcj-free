import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CODEX_BROWSER = ROOT / "core" / "roxy_codex_oauth.py"
CODEX_CONFIG = ROOT / "config" / "codex.py"


def function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(text, node) or ""


class BrowserMfaCompletionTests(unittest.TestCase):
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

    def test_codex_is_enabled_and_required_by_default(self):
        text = CODEX_CONFIG.read_text(encoding="utf-8")
        self.assertIn("ENABLE_CODEX_AUTO: bool = True", text)
        self.assertIn("CODEX_REQUIRED_ON_REGISTRATION: bool = True", text)


if __name__ == "__main__":
    unittest.main()
