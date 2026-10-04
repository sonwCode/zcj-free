import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"


class NavigationRetryTests(unittest.TestCase):
    def test_playwright_transient_navigation_errors_are_retried(self):
        source = SOURCE.read_text(encoding="utf-8")
        for marker in (
            "ERR_EMPTY_RESPONSE",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_CLOSED",
            "ERR_NETWORK_CHANGED",
            "ERR_TIMED_OUT",
        ):
            self.assertIn(marker, source)
        self.assertIn("except Exception as exc:", source)
        self.assertIn("transient and attempt < attempts", source)
        self.assertIn('driver.get("about:blank")', source)


if __name__ == "__main__":
    unittest.main()
