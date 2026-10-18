import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "browser_use_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _load_classifier():
    tree = ast.parse(TEXT, filename=str(SOURCE))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_classify_auth_state"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[node.name]


class BrowserUseAuthStateTests(unittest.TestCase):
    def test_password_route_wins_over_generic_otp_copy_and_password_input(self):
        classify = _load_classifier()
        self.assertEqual(
            classify(
                "https://auth.openai.com/create-account/password",
                has_otp=True,
                has_password=True,
                has_otp_route=False,
            ),
            "password",
        )

    def test_explicit_otp_route_wins_over_stale_password_input(self):
        classify = _load_classifier()
        self.assertEqual(
            classify(
                "https://auth.openai.com/email-verification",
                has_otp=True,
                has_password=True,
                has_otp_route=True,
            ),
            "email_verification",
        )

    def test_login_password_route_remains_distinct(self):
        classify = _load_classifier()
        self.assertEqual(
            classify(
                "https://auth.openai.com/log-in/password",
                has_otp=True,
                has_password=True,
                has_otp_route=False,
            ),
            "login_password",
        )

    def test_otp_widget_without_route_is_recognized(self):
        classify = _load_classifier()
        self.assertEqual(
            classify(
                "https://auth.openai.com/continue",
                has_otp=True,
                has_password=False,
                has_otp_route=False,
            ),
            "email_verification",
        )

    def test_quick_state_uses_python_classifier(self):
        tree = ast.parse(TEXT, filename=str(SOURCE))
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "_quick_auth_state"
        )
        body = ast.get_source_segment(TEXT, node) or ""
        self.assertIn("_classify_auth_state(", body)
        self.assertIn("hasOtpRoute", body)


if __name__ == "__main__":
    unittest.main()
