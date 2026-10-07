import ast
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


SOURCE = Path(__file__).parents[1] / "core" / "roxy_codex_oauth.py"


class _Driver:
    def __init__(self, responses=None, pending=0, current_url="https://auth.openai.com/email-verification"):
        self.responses = responses or []
        self.pending = pending
        self.current_url = current_url
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        if "__codexEmailOtpValidateResponses" in script:
            return self.responses
        if "__codexEmailOtpValidatePending" in script:
            return self.pending
        raise AssertionError(f"unexpected execute_script: {script[:80]}")


def _load_helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {
        "_email_otp_validate_snapshot",
        "_codex_otp_state_summary",
        "_codex_otp_input_complete",
        "_codex_auto_submit_started",
    }
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "json": json,
        "urlparse": urlparse,
        "_is_email_verification_page": lambda driver: "email-verification" in driver.current_url,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


class RoxyCodexOtpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_helpers()

    def test_single_and_multi_field_lengths_are_checked_without_values(self):
        complete = self.helpers["_codex_otp_input_complete"]
        self.assertTrue(complete({"inputs": [{"otp": True, "value_length": 6}]}, "123456"))
        self.assertFalse(complete({"inputs": [{"otp": True, "value_length": 5}]}, "123456"))
        self.assertTrue(
            complete(
                {"inputs": [{"otp": True, "value_length": 1} for _ in range(6)]},
                "123456",
            )
        )
        self.assertFalse(
            complete(
                {"inputs": [{"otp": True, "value_length": 1}, {"otp": True, "value_length": 0}]},
                "123456",
            )
        )

    def test_state_summary_strips_query_and_does_not_include_input_values(self):
        summary = self.helpers["_codex_otp_state_summary"](
            {
                "url": "https://auth.openai.com/email-verification?state=SECRET&code=123456",
                "visible_input_count": 2,
                "inputs": [
                    {
                        "otp": True,
                        "index": 0,
                        "type": "text",
                        "name": "code",
                        "id": "otp",
                        "autocomplete": "one-time-code",
                        "inputmode": "numeric",
                        "maxlength": 6,
                        "value_length": 6,
                        "value": "123456",
                        "aria_invalid": "",
                        "disabled": False,
                        "focused": True,
                    }
                ],
                "forms": [{"action": "/email-verification", "method": "post", "submit_count": 1}],
                "buttons": [{"type": "submit", "action": "verify", "disabled": False}],
            }
        )
        rendered = repr(summary)
        self.assertEqual(summary["url"], "/email-verification")
        self.assertNotIn("SECRET", rendered)
        self.assertNotIn("123456", rendered)
        self.assertEqual(summary["otp_inputs"][0]["value_length"], 6)

    def test_validate_snapshot_exposes_metadata_only(self):
        snapshot = self.helpers["_email_otp_validate_snapshot"](
            _Driver(
                responses=[
                    {
                        "status": 422,
                        "body": json.dumps(
                            {
                                "code": "123456",
                                "error": {"code": "invalid_email_otp", "message": "secret body"},
                            }
                        ),
                    }
                ],
                pending=0,
            )
        )
        rendered = repr(snapshot)
        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["last"]["status"], 422)
        self.assertEqual(snapshot["last"]["body_length"], len(json.dumps({"code": "123456", "error": {"code": "invalid_email_otp", "message": "secret body"}})))
        self.assertIn("error", snapshot["last"]["json_keys"])
        self.assertIn("code", snapshot["last"]["error_keys"])
        self.assertNotIn("123456", rendered)
        self.assertNotIn("secret body", rendered)

    def test_auto_submit_is_detected_from_response_or_navigation(self):
        auto_submit = self.helpers["_codex_auto_submit_started"]
        self.assertTrue(auto_submit(_Driver(responses=[{"status": 422}], current_url="https://auth.openai.com/email-verification"), 0))
        self.assertTrue(auto_submit(_Driver(pending=1), 0))
        self.assertTrue(auto_submit(_Driver(current_url="https://auth.openai.com/u/add-phone"), 0))
        self.assertFalse(auto_submit(_Driver(), 0))

    def test_phone_otp_uses_verified_native_fill(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("def _fill_phone_otp(driver, code: str) -> dict:", source)
        self.assertIn("_codex_otp_input_matches(driver, normalized_code)", source)
        self.assertIn("_set_codex_otp_dom_value(driver, normalized_code)", source)
        phone_flow = source[source.index("def _do_phone_verification_if_present"):]
        self.assertLess(
            phone_flow.index("_install_phone_otp_validate_hook(driver)"),
            phone_flow.index("phone_otp_info = _fill_phone_otp(driver, sms_code)"),
        )
        self.assertIn("_wait_for_phone_otp_auto_submit", phone_flow)
        self.assertIn("if not auto_submitted:", phone_flow)
        self.assertNotIn("_type_otp(driver, sms_code)", phone_flow)
        self.assertIn("phone_otp_input_sync_failed", phone_flow)
        self.assertIn("/api/accounts/phone-otp/validate", source)

    def test_hook_is_installed_before_typing_and_click_is_guarded(self):
        source = SOURCE.read_text(encoding="utf-8")
        flow = source[source.index("def _fill_email_and_otp"):source.index("def _wait_for_fresh_email_otp")]
        self.assertLess(flow.index("_install_email_otp_validate_hook(driver)"), flow.index("_type_otp(driver, code)"))
        self.assertIn("_wait_for_codex_auto_submit", flow)
        self.assertIn("if not submitted:", flow)
        self.assertIn("__codexEmailOtpValidatePending", source)
        fetch_hook = source[source.index("window.fetch = function"):source.index("const origOpen = XMLHttpRequest.prototype.open")]
        self.assertLess(
            fetch_hook.index("__codexEmailOtpValidatePending ="),
            fetch_hook.index("const request = origFetch.apply"),
        )


if __name__ == "__main__":
    unittest.main()
