import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
SMS = ROOT / "core" / "sms_provider.py"


def _body(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found")


def _load_classifier():
    text = SMS.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SMS))
    wanted = {"_classify_failure"}
    classes = {
        n.name
        for n in tree.body
        if isinstance(n, ast.ClassDef)
    }
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in classes:
            body.append(node)
    ns: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SMS), "exec"), ns)  # noqa: S102
    return ns["_classify_failure"]


class ChannelSelectFailureMarkerTests(unittest.TestCase):
    """回归：SMS 选项点不中不该被当成号码质量问题而拉黑好号码。"""

    def test_marker_is_distinct_from_whatsapp_channel(self):
        body = _body(CODEX, "_select_sms_channel_or_raise")
        self.assertIn("sms_channel_select_failed", body)
        # 只有「页面仅提供 WhatsApp」才允许继续用 whatsapp_channel
        self.assertIn("whatsapp_channel: 页面仅提供 WhatsApp 通道", body)
        self.assertEqual(body.count("whatsapp_channel"), 2)

    def test_only_whatsapp_branch_keeps_original_marker(self):
        body = _body(CODEX, "_select_sms_channel_or_raise")
        only_whatsapp_at = body.index("whatsapp_channel: 页面仅提供 WhatsApp 通道")
        new_marker_at = body.index("sms_channel_select_failed")
        self.assertLess(only_whatsapp_at, new_marker_at)

    def test_marker_does_not_blacklist_the_number(self):
        classify = _load_classifier()
        category = classify("sms_channel_select_failed: SMS 通道未被勾选（与号码质量无关）")
        self.assertEqual(category, "unknown")
        self.assertNotIn(category, ("number_rejected", "send_failed", "code_rejected"))

    def test_only_whatsapp_still_blacklists(self):
        """仅 WhatsApp 通道可能与号码所属地区有关，保留原有归类。"""
        classify = _load_classifier()
        self.assertEqual(classify("whatsapp_channel: 页面仅提供 WhatsApp 通道"), "number_rejected")

    def test_marker_is_retryable_in_codex_loop(self):
        text = CODEX.read_text(encoding="utf-8")
        self.assertIn('"sms_channel_select_failed",', text)


if __name__ == "__main__":
    unittest.main()
