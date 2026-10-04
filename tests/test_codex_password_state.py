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


if __name__ == "__main__":
    unittest.main()
