import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _load_profile_helpers():
    tree = ast.parse(TEXT, filename=str(SOURCE))
    names = {"_is_email_verified_page", "_complete_profile_page"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {
        "time": __import__("time"),
        "_check_manual_stop": lambda: None,
        "_stop_aware_sleep": lambda _seconds: None,
        "_has_access_token": lambda _driver: False,
        "_page_snapshot": lambda driver: driver.snapshots.pop(0),
        "_is_profile_like": lambda snap: bool(snap.get("profile")),
        "_log_prefix": lambda _driver: "[test]",
        "logger": Mock(),
        "driver": None,
    }
    # _complete_profile_page references more helpers only on the profile path.
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class EmailVerifiedProfileNavigationTests(unittest.TestCase):
    def test_recognizes_job_67_email_verified_snapshot(self):
        ns = _load_profile_helpers()
        self.assertTrue(ns["_is_email_verified_page"]({
            "url": "https://auth.openai.com/email-verification",
            "title": "Email verified - OpenAI",
            "text": "Email verified\nYour email (user@example.com) has already been verified",
            "inputs": [], "buttons": [],
        }))

    def test_recovers_once_then_reports_navigation_stall(self):
        ns = _load_profile_helpers()
        driver = Mock()
        verified = {
            "url": "https://auth.openai.com/email-verification",
            "title": "Email verified - OpenAI",
            "text": "Email verified\nYour email (user@example.com) has already been verified",
            "inputs": [], "buttons": [],
        }
        driver.snapshots = [dict(verified) for _ in range(8)]
        with patch.dict(ns, {"_page_snapshot": lambda _driver: dict(verified)}):
            with self.assertRaisesRegex(RuntimeError, "邮箱已验证但资料页导航停滞"):
                ns["_complete_profile_page"](driver, "Test User", "1990-01-01", timeout=1)

    def test_email_verified_page_is_not_treated_as_profile_success(self):
        body = next(ast.get_source_segment(TEXT, n) for n in ast.parse(TEXT).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_complete_profile_page")
        self.assertIn("driver.refresh()", body)
        self.assertIn("邮箱已验证但资料页导航停滞", body)
        self.assertNotIn("return True", body[body.index("if _is_email_verified_page(snap):"):body.index("if not _is_profile_like(snap):")])


if __name__ == "__main__":
    unittest.main()
