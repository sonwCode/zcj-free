import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CODEX = ROOT / "core" / "roxy_codex_oauth.py"
CFG = ROOT / "config" / "codex.py"
TEXT = CODEX.read_text(encoding="utf-8")


def _load(names):
    tree = ast.parse(TEXT, filename=str(CODEX))
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
            found = [t.id for t in tgts if isinstance(t, ast.Name)]
            if any(n.startswith("_PHONE_") for n in found):
                body.append(node)
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(CODEX), "exec"), ns)  # noqa: S102
    return ns


class ResubmitClassificationTests(unittest.TestCase):
    """失败先原地重提交，而不是立刻换号。"""

    def test_page_did_not_advance_is_repeatable(self):
        fn = _load({"_is_repeatable_phone_submit_error"})["_is_repeatable_phone_submit_error"]
        for text in (
            "send_not_accepted: 提交后仍停留在 add-phone",
            "whatsapp_channel_reverted: ...",
            "phone_number_required: Phone number required",
            "sms_channel_select_failed: SMS 通道未被勾选",
        ):
            with self.subTest(text=text):
                self.assertTrue(fn(RuntimeError(text)))

    def test_number_rejected_is_not_repeatable(self):
        fn = _load({"_is_repeatable_phone_submit_error"})["_is_repeatable_phone_submit_error"]
        for text in (
            "invalid_phone: add-phone input aria-invalid",
            "invalid_phone_code: Check your phone",
            "delivery_refused: cannot send",
            "whatsapp_channel: 页面仅提供 WhatsApp 通道",
            "invalid_auth_step: ...",
        ):
            with self.subTest(text=text):
                self.assertFalse(fn(RuntimeError(text)))

    def test_empty_error_is_not_repeatable(self):
        fn = _load({"_is_repeatable_phone_submit_error"})["_is_repeatable_phone_submit_error"]
        self.assertFalse(fn(RuntimeError("")))
        self.assertFalse(fn(None))


class ChannelMarkerSplitTests(unittest.TestCase):
    """SMS 选项存在却回退到 WhatsApp，与页面只有 WhatsApp 是两种问题。"""

    def _classify(self):
        # 分类器依赖 _is_phone_code_state，夹具必须一并提取，否则会在依赖处 NameError。
        return _load({"_classify_phone_page_failure", "_is_phone_code_state"})["_classify_phone_page_failure"]

    def test_sms_option_present_but_whatsapp_checked_is_reverted(self):
        classify = self._classify()
        state = {
            "radios": [
                {"value": "sms", "checked": False},
                {"value": "whatsapp", "checked": True},
            ],
            "inputs": [{"type": "tel", "name": "", "value": "+1234"}],
            "bodyText": "",
        }
        self.assertEqual(classify(state), "whatsapp_channel_reverted")

    def test_only_whatsapp_offered_keeps_original_marker(self):
        classify = self._classify()
        state = {
            "radios": [{"value": "whatsapp", "checked": True}],
            "inputs": [],
            "bodyText": "",
        }
        self.assertEqual(classify(state), "whatsapp_channel")


class StateDigestTests(unittest.TestCase):
    def test_digest_shows_channel_and_field_state(self):
        fn = _load({"_phone_state_digest"})["_phone_state_digest"]
        out = fn({
            "radios": [{"value": "sms", "checked": True}, {"value": "whatsapp", "checked": False}],
            "inputs": [{"type": "tel", "name": "", "value": "+123456", "ariaInvalid": ""}],
        })
        self.assertIn("sms=Y", out)
        self.assertIn("whatsapp=n", out)
        self.assertIn("invalid=-", out)

    def test_digest_handles_empty_state(self):
        fn = _load({"_phone_state_digest"})["_phone_state_digest"]
        self.assertIn("channels=[-]", fn({}))
        self.assertIn("invalid", fn(None))


class ResubmitLoopWiringTests(unittest.TestCase):
    def test_loop_uses_extracted_helper(self):
        self.assertIn("_prepare_and_submit_add_phone(", TEXT)
        self.assertIn("_is_repeatable_phone_submit_error(send_exc)", TEXT)

    def test_submit_rounds_is_configurable(self):
        self.assertIn("SMS_NUMBER_SUBMIT_ROUNDS", CFG.read_text(encoding="utf-8"))
        self.assertIn("'SMS_NUMBER_SUBMIT_ROUNDS': 'int'", CFG.read_text(encoding="utf-8"))

    def test_no_bare_cfg_reference(self):
        """历史教训：_cfg 未定义会抛 NameError 并被宽泛 except 吞掉。"""
        # proto._cfg / sms_provider._cfg / _roxy_cfg 都指向别的模块命名空间，合法；
        # 只有裸 _cfg 才是本模块里未定义的名字。
        allowed_prefixes = ("_roxy_cfg", "sms_provider._cfg", "proto._cfg", "sms_cfg")
        for i, line in enumerate(TEXT.splitlines(), 1):
            if "_cfg." not in line:
                continue
            if any(p in line for p in allowed_prefixes):
                continue
            self.fail(f"疑似未定义的 _cfg 引用 line {i}: {line.strip()[:80]}")


if __name__ == "__main__":
    unittest.main()
