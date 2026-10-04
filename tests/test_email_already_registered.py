import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
ROXY = ROOT / "core" / "roxy_registration.py"
DB = ROOT / "core" / "db.py"
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
CLOAK = ROOT / "core" / "cloakbrowser_registration.py"
ROXY_TXT = ROXY.read_text(encoding="utf-8")


def _fn(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {path}")


class PasswordPersistedEarlyTests(unittest.TestCase):
    """方案 A：密码一设置成功就写回邮箱素材，任务中途失败也不丢。"""

    def test_db_exposes_pool_password_api(self):
        src = DB.read_text(encoding="utf-8")
        self.assertIn("def remember_pool_password(", src)
        self.assertIn("def get_pool_password(", src)
        self.assertIn("_PASSWORD_POOL_COLLECTIONS", src)

    def test_db_writes_into_full_payload_not_copy_line(self):
        body = _fn(DB, "remember_pool_password")
        self.assertIn('row["registration_password"]', body)
        self.assertIn("registration_password_saved_at", body)
        self.assertIn("_save_collection(collection, rows)", body)

    def test_password_written_right_after_submit(self):
        body = _fn(ROXY, "_fill_password_page_if_present")
        submit_at = body.index("已填写并点击密码页 Continue")
        persist_at = body.index("remember_pool_password")
        wait_at = body.index("wait_end = time.time() + 60")
        self.assertLess(submit_at, persist_at, "写回必须在点击提交之后")
        self.assertLess(persist_at, wait_at, "写回必须在等待跳转之前")

    def test_codex_reads_pool_password_as_last_resort(self):
        body = _fn(CODEX, "_account_password_for_email")
        self.assertIn("_REGISTRATION_PASSWORD_CACHE", body)
        self.assertIn("_account_registration_password", body)
        self.assertIn("get_pool_password", body)
        self.assertLess(body.index("_account_registration_password"), body.index("get_pool_password"))


class AlreadyRegisteredHandlingTests(unittest.TestCase):
    """方案 B/C：登录密码页按『已注册邮箱』处理，不再产出无密码账号。"""

    def test_exception_is_defined_and_used(self):
        self.assertIn("class EmailAlreadyRegistered(RuntimeError)", ROXY_TXT)
        self.assertEqual(ROXY_TXT.count("raise EmailAlreadyRegistered"), 5)

    def test_all_login_password_guards_use_the_exception(self):
        self.assertNotIn('raise RuntimeError(f"邮箱提交后进入登录密码页', ROXY_TXT)
        self.assertEqual(ROXY_TXT.count("邮箱在 OpenAI 侧已存在账号（邮箱提交后进入登录密码页）"), 4)

    def test_login_page_prefers_saved_password(self):
        body = _fn(ROXY, "_fill_password_page_if_present")
        self.assertIn("_known_pool_password(email)", body)
        self.assertIn("_fill_login_password_on_page", body)
        self.assertIn("raise EmailAlreadyRegistered(", body)

    def test_passwordless_fallback_removed_from_login_branch(self):
        """旧逻辑会点“一次性验证码登录”静默降级，产出无密码账号。"""
        body = _fn(ROXY, "_fill_password_page_if_present")
        login_at = body.index("if is_login_password:")
        signup_at = body.index("password = _registration_password()")
        login_branch = body[login_at:signup_at]
        self.assertNotIn("_click_passwordless_signup_if_present", login_branch)

    def test_login_password_helper_targets_current_password_field(self):
        body = _fn(ROXY, "_fill_login_password_on_page")
        self.assertIn("current-password", body)
        self.assertIn("_click_continue(driver)", body)

    def test_registered_email_is_marked_failed_on_release(self):
        for path, fn_name, label in (
            (ROXY, "run_roxy_registration", "roxy"),
            (CLOAK, "_run_cloak_registration_impl", "cloak"),
        ):
            with self.subTest(module=label):
                body = _fn(path, fn_name)
                self.assertIn("isinstance(exc, EmailAlreadyRegistered)", body)
                self.assertIn('release_email(email, status="failed", note=f"邮箱已注册', body)


if __name__ == "__main__":
    unittest.main()
