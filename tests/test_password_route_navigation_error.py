import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"


def _load_helpers():
    """从源码中提取本测试需要的纯函数与常量，避免导入浏览器依赖。"""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    wanted = {"_is_navigation_error_url", "_NAVIGATION_ERROR_URL_TOKENS"}
    body = [
        node for node in tree.body
        if (
            isinstance(node, (ast.FunctionDef, ast.Assign))
            and (
                (isinstance(node, ast.FunctionDef) and node.name in wanted)
                or (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id in wanted
                        for t in node.targets
                    )
                )
            )
        )
    ]
    module = ast.Module(body=body, type_ignores=[])
    namespace: dict = {}
    exec(compile(module, str(SOURCE), "exec"), namespace)  # noqa: S102 - 受控源码
    return namespace


class NavigationErrorUrlTests(unittest.TestCase):
    """Job 39 回归：上游 500 把跨主机导航截断后，不得放行到 OTP 阶段。"""

    def test_chromium_error_page_urls_are_detected(self):
        ns = _load_helpers()
        is_error = ns["_is_navigation_error_url"]
        for url in (
            "chrome-error://chromewebdata/",
            "chrome-error://chromewebdata",
            "CHROME-ERROR://CHROMEWEBDATA/",
            "edge-error://chromewebdata/",
            "about:neterror?e=http2_protocol_error",
            " about:error ",
        ):
            with self.subTest(url=url):
                self.assertTrue(is_error(url), url)

    def test_real_pages_are_not_treated_as_navigation_errors(self):
        ns = _load_helpers()
        is_error = ns["_is_navigation_error_url"]
        for url in (
            "https://chatgpt.com/auth/login",
            "https://auth.openai.com/create-account/password",
            "https://chatgpt.com/api/auth/session",
            "",
            None,
        ):
            with self.subTest(url=url):
                self.assertFalse(is_error(url), str(url))

    def test_password_route_guard_no_longer_gates_on_url_substring(self):
        """password_route_requested 已翻真时必须直接拦截，不能再看 URL 子串。"""
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("if password_route_requested:", source)
        self.assertNotIn(
            'if password_route_requested and any(x in current_url.lower() for x in (',
            source,
        )

    def test_password_route_guard_reopens_login_entry_on_navigation_error(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("_is_navigation_error_url(current_url)", source)
        self.assertIn('reason="password_route_navigation_error"', source)
        self.assertIn("导航可能被上游错误截断", source)


if __name__ == "__main__":
    unittest.main()
