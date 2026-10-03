import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
ROXY = ROOT / "core" / "roxy_registration.py"
CLOAK = ROOT / "core" / "cloakbrowser_registration.py"


class ChatGPTSessionRecoveryTests(unittest.TestCase):
    def test_roxy_waits_for_callback_and_has_bounded_recovery(self):
        source = ROXY.read_text(encoding="utf-8")
        self.assertIn("auto_jump_wait: int = 45", source)
        self.assertIn("_read_chatgpt_session_via_context", source)
        self.assertIn("cache-control", source)
        self.assertIn("warning_only_rounds >= 20", source)
        self.assertIn("_recover_chatgpt_login_session", source)
        self.assertIn("session_refreshes < 2", source)

    def test_registration_passes_credentials_to_session_recovery(self):
        roxy = ROXY.read_text(encoding="utf-8")
        cloak = CLOAK.read_text(encoding="utf-8")
        for source in (roxy, cloak):
            self.assertIn("auto_jump_wait=45", source)
            self.assertIn("email=email", source)
            self.assertIn("password=openai_password", source)

    def test_recovery_is_not_unbounded(self):
        source = ROXY.read_text(encoding="utf-8")
        self.assertIn("login_recovery_attempted = False", source)
        self.assertIn("login_recovery_attempted = True", source)
        self.assertIn("and not login_recovery_attempted", source)

    def test_remail_and_legacy_provider_compatibility_is_present(self):
        remail = (ROOT / "core" / "remail_client.py").read_text(encoding="utf-8")
        provider = (ROOT / "core" / "email_provider.py").read_text(encoding="utf-8")
        self.assertIn("exclude_codes: set[str] | None = None", remail)
        self.assertIn("code not in excluded_codes", remail)
        self.assertIn("legacy_kwargs.pop(\"exclude_codes\", None)", provider)


if __name__ == "__main__":
    unittest.main()
