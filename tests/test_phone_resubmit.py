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


class ImmediateReleaseWiringTests(unittest.TestCase):
    """每个已采购号码只提交一次；异常会回到统一释放分支。"""

    def test_cloak_email_otp_matches_reference_retry_contract(self):
        source = (ROOT / "core" / "cloakbrowser_registration.py").read_text(encoding="utf-8")
        self.assertIn("max_otp_attempts = 3", source)
        self.assertIn("after_ts=0.0, max_wait=15, poll_interval=3", source)
        self.assertIn("_wait_after_email_otp_submit(driver, timeout=30)", source)

    def test_cloak_isolated_worker_timeout_returns_terminal_result(self):
        source = (ROOT / "core" / "cloakbrowser_registration.py").read_text(encoding="utf-8")
        self.assertIn("thread.join(timeout)", source)
        self.assertIn('"error_code": "cloak_worker_join_timeout"', source)
        self.assertIn('"task_status": "failed"', source)
        self.assertIn("threading.Thread(target=_target, name=parent_thread_name, daemon=True)", source)

    def test_codex_password_uses_registration_password_and_fails_fast(self):
        self.assertIn("registration_password: str | None = None", TEXT)
        self.assertIn("registration_password=registration_password", TEXT)
        self.assertIn("codex_password_step_stalled", TEXT)
        self.assertIn("if \"codex_password_step_stalled\" in str(exc):", TEXT)

    def test_same_activation_resubmit_helper_is_removed(self):
        self.assertNotIn("_is_repeatable_phone_submit_error", TEXT)
        self.assertNotIn("submit_rounds", TEXT)

    def test_channel_failure_is_in_switch_path(self):
        switch_start = TEXT.index("_PHONE_SWITCH_HINTS")
        switch_block = TEXT[switch_start:TEXT.index("def _prepare_and_submit_add_phone", switch_start)]
        self.assertIn("whatsapp_channel_reverted", switch_block)
        self.assertIn("sms_channel_select_failed", switch_block)

    def test_phone_failure_is_released_before_retry(self):
        acquire = TEXT.index("activation_id, phone = sms_provider.acquire_number(http)")
        release = TEXT.index("sms_provider.cancel_and_report_failure(activation_id, http, exc)")
        self.assertLess(acquire, release)
        self.assertIn("_prepare_and_submit_add_phone(", TEXT)


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
    def test_loop_submits_each_activation_once(self):
        self.assertIn("_prepare_and_submit_add_phone(", TEXT)
        self.assertNotIn("_is_repeatable_phone_submit_error", TEXT)
        self.assertNotIn("submit_rounds", TEXT)
        self.assertNotIn("SMS_NUMBER_SUBMIT_ROUNDS", CFG.read_text(encoding="utf-8"))

    def test_retry_acquires_a_new_activation_after_release(self):
        loop_start = TEXT.index("def _do_phone_verification_if_present")
        loop_block = TEXT[loop_start:]
        acquire = loop_block.index("activation_id, phone = sms_provider.acquire_number(http)")
        release = loop_block.index("sms_provider.cancel_and_report_failure(activation_id, http, exc)")
        next_attempt = loop_block.index("for attempt in range(1, max_retries + 1)")
        self.assertLess(next_attempt, acquire)
        self.assertLess(acquire, release)

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
