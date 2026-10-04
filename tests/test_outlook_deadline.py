import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "outlook_client.py"
TEXT = SOURCE.read_text(encoding="utf-8")


class OutlookDeadlineTests(unittest.TestCase):
    def test_poll_loop_bounds_session_timeout_by_deadline(self):
        tree = ast.parse(TEXT)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch_latest_otp")
        body = ast.get_source_segment(TEXT, fn) or ""
        self.assertIn("remaining_before_round = deadline - time.time()", body)
        self.assertIn("session.timeout = max(1, min(30, int(deadline - time.time())))", body)
        self.assertIn("if deadline - time.time() <= 0", body)

    def test_default_http_session_still_has_thirty_second_cap(self):
        self.assertIn("s.timeout = 30", TEXT)


if __name__ == "__main__":
    unittest.main()
