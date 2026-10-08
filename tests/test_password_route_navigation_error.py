import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def load_helpers():
    tree = ast.parse(TEXT)
    wanted = {"_is_navigation_error_url", "_NAVIGATION_ERROR_URL_TOKENS"}
    body = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets))
    ]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class NavigationErrorUrlTests(unittest.TestCase):
    def test_chromium_error_urls_are_detected(self):
        is_error = load_helpers()["_is_navigation_error_url"]
        for url in ("chrome-error://chromewebdata/", "edge-error://chromewebdata/", "about:neterror?e=http2_protocol_error"):
            self.assertTrue(is_error(url), url)

    def test_real_pages_are_not_navigation_errors(self):
        is_error = load_helpers()["_is_navigation_error_url"]
        for url in ("https://chatgpt.com/auth/login", "https://auth.openai.com/create-account/password", ""):
            self.assertFalse(is_error(url), url)

    def test_password_flow_bounds_recovery_and_keeps_navigation_guard(self):
        tree = ast.parse(TEXT)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_fill_password_page_if_present")
        body = ast.get_source_segment(TEXT, node) or ""
        self.assertIn("_recovery_round >= 1", body)
        self.assertIn("password_route_requested and any", body)
        self.assertIn("_is_navigation_error_url(current_url)", body)


if __name__ == "__main__":
    unittest.main()
