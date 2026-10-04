import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
CLOAK = ROOT / "core" / "cloakbrowser_registration.py"
ROXY = ROOT / "core" / "roxy_registration.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RegistrationPasswordHandoffTests(unittest.TestCase):
    """回归：注册后立刻跑 Codex 时，必须把刚设好的密码传给 Codex 授权。

    账号要到注册收尾才写入数据库，而 Codex 登录密码页通过邮箱查库取密码。
    不显式传递时查库为空，密码登录被静默跳过，降级成一次性验证码登录，
    服务端连续返回 Incorrect code 并最终封掉 max_check_attempts（Job 41）。
    """

    def test_codex_entry_accepts_registration_password(self):
        source = _source(CODEX)
        self.assertIn("registration_password: str | None = None", source)
        self.assertIn("def remember_registration_password(", source)

    def test_password_cache_lookup_precedes_database(self):
        """缓存必须优先于查库，否则账号未落库时仍然取不到密码。"""
        tree = ast.parse(_source(CODEX), filename=str(CODEX))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_account_password_for_email":
                segment = ast.get_source_segment(_source(CODEX), node) or ""
                cached_at = segment.index("_REGISTRATION_PASSWORD_CACHE")
                db_at = segment.index("_codex_proto._account_registration_password")
                self.assertLess(cached_at, db_at, "缓存读取必须排在查库之前")
                return
        self.fail("_account_password_for_email 未找到")

    def test_password_lookup_is_case_insensitive(self):
        source = _source(CODEX)
        self.assertIn('.strip().lower()', source)

    def test_both_registration_paths_pass_the_password(self):
        for path in (CLOAK, ROXY):
            with self.subTest(module=path.name):
                source = _source(path)
                self.assertIn("registration_password=openai_password", source)

    def test_password_source_and_handoff_remain_available(self):
        source = _source(CODEX)
        self.assertIn("def _account_password_for_email", source)
        self.assertIn("registration_password: str | None = None", source)


if __name__ == "__main__":
    unittest.main()
