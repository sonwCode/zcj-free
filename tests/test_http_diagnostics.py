import unittest
from types import SimpleNamespace

from core.http_diagnostics import is_cloudflare_challenge, response_summary


class HttpDiagnosticsTests(unittest.TestCase):
    def test_cloudflare_challenge_is_detected_from_response_headers(self):
        response = SimpleNamespace(
            status_code=403,
            headers={
                "Cf-Mitigated": "challenge",
                "CF-Ray": "abc123-LAX",
                "Content-Type": "text/html; charset=utf-8",
            },
            url="https://auth.openai.com/login?secret=not-for-logs",
        )

        self.assertTrue(is_cloudflare_challenge(response))
        summary = response_summary(response)
        self.assertIn("status=403", summary)
        self.assertIn("cf_ray=abc123-LAX", summary)
        self.assertIn("challenge=True", summary)
        self.assertNotIn("secret", summary)

    def test_non_challenge_403_is_not_mislabeled(self):
        response = SimpleNamespace(status_code=403, headers={"Content-Type": "application/json"})

        self.assertFalse(is_cloudflare_challenge(response))
        self.assertIn("challenge=False", response_summary(response))


if __name__ == "__main__":
    unittest.main()
