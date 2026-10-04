import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_codex_oauth.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _source(name):
    tree = ast.parse(TEXT, filename=str(SOURCE))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(name)


class CodexPasswordStateTests(unittest.TestCase):
    def test_password_page_stall_is_not_reported_as_next_step(self):
        body = _source("_fill_login_password_if_present")
        self.assertIn("仍停留登录密码页，拒绝标记为登录成功", body)
        self.assertIn('if _is_login_password_page(driver):', body)
        self.assertIn('return None', body)

    def test_success_still_requires_leaving_password_page(self):
        body = _source("_fill_login_password_if_present")
        submit_at = body.index("已填写并提交登录密码")
        after_submit = body[submit_at:]
        state_at = after_submit.index("if not _is_login_password_page(driver):")
        self.assertIn('return "next_step"', after_submit[state_at:])
        self.assertIn("拒绝标记为登录成功", after_submit[state_at:])

    def test_job_72_failure_url_is_not_accepted_as_success(self):
        body = _source("_fill_login_password_if_present")
        # The stale password route must remain a guarded failure/None path.
        self.assertNotIn('if not _is_login_password_page(driver):\n                return "next_step"\n            _stop_sleep(0.5)\n        return "next_step"', body)

    def test_password_stall_logs_page_diagnostics(self):
        body = _source("_fill_login_password_if_present")
        self.assertIn("密码页超时页面诊断", body)
        self.assertIn("document.body?.innerText", body)
        self.assertIn("inputs:", body)
        self.assertIn("buttons:", body)

    def test_post_email_auth_entry_wait_has_explicit_states(self):
        body = _source("_wait_for_codex_auth_entry_state")
        self.assertIn('return "mfa"', body)
        self.assertIn('return "email_otp"', body)
        self.assertIn('return "password"', body)
        self.assertIn('return "unknown"', body)

    def test_initial_codex_email_flow_matches_reference_call_order(self):
        body = _source("_fill_email_and_otp")
        submit_at = body.index("_submit_email_step(driver)")
        password_at = body.index("_fill_login_password_if_present(driver, email, timeout=18)")
        self.assertLess(submit_at, password_at)
        self.assertIn("按参考流程等待密码页或验证码页", body)
        self.assertNotIn("仍停留 /log-in，执行邮箱表单重提交", body)

    def test_password_uses_selected_reference_button_handles(self):
        body = _source("_fill_login_password_if_present")
        self.assertIn("input, button:target", body)
        self.assertIn('_human_type_text(driver, result.get("input")', body)
        self.assertIn('_human_click(driver, result.get("button")', body)
        self.assertIn("codex_password_submit", body)

    def test_shared_email_submit_confirms_navigation_and_recovers_stall(self):
        registration_text = (ROOT / "core" / "roxy_registration.py").read_text(encoding="utf-8")
        tree = ast.parse(registration_text)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_submit_email_step")
        body = ast.get_source_segment(registration_text, node) or ""
        self.assertIn("/log-in", body)
        self.assertIn("_recover_email_submit_if_stuck(driver, email_value)", body)
        self.assertIn("email_submit_stalled", body)

    def test_email_submit_stall_includes_runtime_trace(self):
        registration_text = (ROOT / "core" / "roxy_registration.py").read_text(encoding="utf-8")
        self.assertIn("__roxy_email_submit_trace", registration_text)
        self.assertIn("submit_attempt", registration_text)
        self.assertIn("record.status=response.status", registration_text)
        self.assertIn("trace={trace}", registration_text)


if __name__ == "__main__":
    unittest.main()
