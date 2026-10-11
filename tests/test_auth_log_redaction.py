# -*- coding: utf-8 -*-
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class AuthLogRedactionTests(unittest.TestCase):
    def test_twofa_logs_do_not_render_credentials(self):
        source = (ROOT / "core" / "account_export.py").read_text(encoding="utf-8")
        forbidden = (
            "new_token[:",
            "secret[:",
            "continue_url=%s",
            "session_id=%s",
            "reauth authorize URL: {resp.text}",
            "enroll 失败 {resp.status_code}: {resp.text}",
            "activate 失败 {resp.status_code}: {resp.text}",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)
        self.assertIn("OAuth callback=<redacted>", source)
        self.assertIn("secret=<redacted>", source)

    def test_twofa_service_logs_only_redacted_secret(self):
        source = (ROOT / "core" / "twofa_service.py").read_text(encoding="utf-8")
        self.assertNotIn("secret[:", source)
        self.assertIn("secret=<redacted>", source)

    def test_circuit_breaker_drops_query_values(self):
        source = (ROOT / "core" / "session.py").read_text(encoding="utf-8")
        self.assertIn("safe_url = _safe_url_for_log(url)", source)
        self.assertIn('query="", fragment=""', source)
        self.assertIn('blocked_reason = f"HTTP {status} from {safe_url}"', source)

    def test_protocol_auth_errors_do_not_log_raw_bodies(self):
        source = (ROOT / "core" / "openai_auth.py").read_text(encoding="utf-8")
        self.assertNotIn("user/register 失败 status=%s body=%s", source)
        self.assertNotIn("响应内容: {resp.text}", source)
        self.assertNotIn("响应摘要", source)
        self.assertIn("content_length=%s", source)


if __name__ == "__main__":
    unittest.main()
