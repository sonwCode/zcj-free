import ast
import time
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
REG = ROOT / "core" / "roxy_registration.py"


def source(path, name):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(text, node) or ""


def load_email_flow(stubs):
    text = CODEX.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_fill_email_and_otp")
    namespace = {
        "time": time,
        "logger": Mock(),
        "_safe_url_for_log": lambda value: "<url>",
        "_mask_otp": lambda value: "<redacted>",
        "human_delay": Mock(),
        "_maybe_accept": Mock(),
        "_type_email_address": Mock(),
        "_submit_email_step": Mock(),
        "_fill_login_password_if_present": Mock(return_value=None),
        "_wait_for_codex_auth_entry_state": Mock(return_value="unknown"),
        "_is_login_password_page": lambda driver: False,
        "_is_email_verification_page": lambda driver: False,
        "_is_mfa_challenge_page": lambda driver: False,
        "_fill_mfa_challenge_if_present": Mock(),
        "_maybe_click_passwordless_after_email": Mock(),
    }
    namespace.update(stubs)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(CODEX), "exec"), namespace)
    return namespace["_fill_email_and_otp"], namespace


class ReferenceAuthContractTests(unittest.TestCase):
    def test_reference_codex_auth_flow_order(self):
        body = source(CODEX, "_fill_email_and_otp")
        self.assertLess(body.index("_submit_email_step(driver)"), body.index("_fill_login_password_if_present("))
        self.assertIn("_maybe_click_passwordless_after_email", body)
        self.assertIn("_wait_for_fresh_email_otp", body)
        self.assertIn("已排除旧码=%s", body)
        self.assertIn("validate_snapshot = _email_otp_validate_snapshot(driver)", body)

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

    def test_email_submit_race_on_password_page_retries_password_step(self):
        submit = Mock(side_effect=RuntimeError("email_submit_stalled: still at login page"))
        password = Mock(return_value="next_step")
        flow, _ = load_email_flow({
            "_submit_email_step": submit,
            "_fill_login_password_if_present": password,
            "_is_login_password_page": lambda driver: True,
        })

        class Driver:
            current_url = "https://auth.openai.com/log-in/password"

            def get(self, url):
                self.current_url = url

        driver = Driver()
        flow(driver, "user@example.test", Mock(), "https://auth.openai.com/oauth/authorize", registration_password="secret")

        password.assert_called_once_with(
            driver,
            "user@example.test",
            timeout=18,
            registration_password="secret",
        )

    def test_delayed_login_page_transitions_to_password_step(self):
        submit = Mock(side_effect=RuntimeError("email_submit_stalled: delayed navigation"))
        wait_for_entry = Mock(return_value="password")
        password = Mock(return_value="next_step")
        flow, _ = load_email_flow({
            "_submit_email_step": submit,
            "_wait_for_codex_auth_entry_state": wait_for_entry,
            "_fill_login_password_if_present": password,
            "_is_login_password_page": lambda driver: False,
            "_is_email_verification_page": lambda driver: False,
        })

        class Driver:
            current_url = "https://auth.openai.com/log-in"

            def get(self, url):
                self.current_url = url

        driver = Driver()
        flow(
            driver,
            "user@example.test",
            Mock(),
            "https://auth.openai.com/oauth/authorize",
            registration_password="secret",
        )

        wait_for_entry.assert_called_once_with(driver, timeout=24)
        password.assert_called_once_with(
            driver,
            "user@example.test",
            timeout=18,
            registration_password="secret",
        )

    def test_stalled_login_page_gets_one_fresh_authorize_recovery(self):
        submit = Mock(side_effect=[RuntimeError("email_submit_stalled: still at login page"), None])
        password = Mock(return_value="next_step")
        flow, _ = load_email_flow({
            "_submit_email_step": submit,
            "_fill_login_password_if_present": password,
            "_wait_for_codex_auth_entry_state": Mock(return_value="unknown"),
            "_is_login_password_page": lambda driver: False,
            "_is_email_verification_page": lambda driver: False,
        })

        class Driver:
            current_url = "https://auth.openai.com/log-in"
            gets = []

            def get(self, url):
                self.gets.append(url)
                self.current_url = "https://auth.openai.com/log-in"

        driver = Driver()
        flow(
            driver=driver,
            email="user@example.test",
            otp_provider=Mock(),
            auth_url="https://auth.openai.com/oauth/authorize",
            registration_password="secret",
        )

        self.assertEqual(submit.call_count, 2)
        self.assertEqual(
            driver.gets,
            ["https://auth.openai.com/oauth/authorize", "https://auth.openai.com/oauth/authorize"],
        )
        password.assert_called_once_with(
            driver,
            "user@example.test",
            timeout=18,
            registration_password="secret",
        )

    def test_stalled_login_page_fails_after_recovery_is_exhausted(self):
        submit = Mock(side_effect=RuntimeError("email_submit_stalled: still at login page"))
        flow, _ = load_email_flow({
            "_submit_email_step": submit,
            "_wait_for_codex_auth_entry_state": Mock(return_value="unknown"),
            "_is_login_password_page": lambda driver: False,
            "_is_email_verification_page": lambda driver: False,
        })

        class Driver:
            current_url = "https://auth.openai.com/log-in"

            def get(self, url):
                # The submit failure is observed after the browser has settled on /log-in.
                self.current_url = "https://auth.openai.com/log-in"

        with self.assertRaisesRegex(RuntimeError, "codex_email_submit_stalled"):
            flow(driver=Driver(), email="user@example.test", otp_provider=Mock(), auth_url="https://auth.openai.com/oauth/authorize")

    def test_email_submit_error_on_otp_page_continues_otp_flow(self):
        submit = Mock(side_effect=RuntimeError("email_submit_stalled: delayed navigation"))
        wait_for_code = Mock(return_value="123456")
        wait_after_submit = Mock(return_value="accepted")
        flow, _ = load_email_flow({
            "_submit_email_step": submit,
            "_is_login_password_page": lambda driver: False,
            "_is_email_verification_page": lambda driver: True,
            "_wait_for_fresh_email_otp": wait_for_code,
            "_wait_for_otp_input": Mock(),
            "_clear_otp_inputs": Mock(),
            "_install_email_otp_validate_hook": Mock(),
            "_email_otp_validate_snapshot": Mock(return_value={"count": 0}),
            "_type_otp": Mock(),
            "_wait_for_codex_auto_submit": Mock(return_value=True),
            "_wait_after_email_otp_submit": wait_after_submit,
        })

        class Driver:
            current_url = "https://auth.openai.com/email-verification"

            def get(self, url):
                self.current_url = url

        driver = Driver()
        flow(driver, "user@example.test", Mock(), "https://auth.openai.com/oauth/authorize")

        wait_for_code.assert_called_once()
        wait_after_submit.assert_called_once_with(driver, timeout=45)

    def test_registration_and_sms_modules_remain_present(self):
        self.assertTrue(REG.exists())
        self.assertTrue((ROOT / "core" / "sms_provider.py").exists())


if __name__ == "__main__":
    unittest.main()
