# -*- coding: utf-8 -*-
"""Codex 邮箱 OTP 取码基准与重开授权返回值的回归测试。

背景（Job 410/411 现场）：
1. Job 411 在 codex 授权阶段把 otp_after_ts 重置为"页面到达验证码页"的时刻
   （12:58:06），而服务端真正发出的那封验证码是提交邮箱时（12:57:07）投递的。
   旧的 30 秒容差把 12:57:07 + 30 < 12:58:06 判为旧码过滤掉，连续 4 轮取码全空。
2. 取码接口的时间字段与本机存在时区差/投递延迟，容差过小同样会误杀本轮验证码。
3. _restart_email_otp_flow 重开授权后已用密码(+MFA)完成登录，但返回值被丢弃，
   外层循环继续等邮箱验证码，白耗剩余轮次直到超时。
"""
import ast
import json
import re
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
TARGET = ROOT / "core" / "roxy_codex_oauth.py"
TEXT = TARGET.read_text(encoding="utf-8")
MAIL_CLIENT = ROOT / "core" / "generic_api_mail_client.py"
MAIL_TEXT = MAIL_CLIENT.read_text(encoding="utf-8")
ROXY_REG = ROOT / "core" / "roxy_registration.py"
ROXY_REG_TEXT = ROXY_REG.read_text(encoding="utf-8")
EMAIL_CFG = ROOT / "config" / "email.py"
EMAIL_CFG_TEXT = EMAIL_CFG.read_text(encoding="utf-8")


def _function_source(path: Path, text: str, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} not found in {path.name}")


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


def _load_tolerance_helper():
    tree = ast.parse(MAIL_TEXT, filename=str(MAIL_CLIENT))
    wanted = {"_after_ts_tolerance"}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "_email_cfg": type("Cfg", (), {"OTP_AFTER_TS_TOLERANCE_SECONDS": 180})(),
        "_AFTER_TS_TOLERANCE_DEFAULT": 180,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(MAIL_CLIENT), "exec"), namespace)
    return namespace["_after_ts_tolerance"]


class OtpToleranceConfigTests(unittest.TestCase):
    def test_tolerance_default_is_wide_enough_for_delivery_skew(self):
        tolerance = _load_tolerance_helper()
        self.assertGreaterEqual(tolerance(), 60.0)

    def test_config_exposes_tolerance_and_env_override(self):
        self.assertIn("OTP_AFTER_TS_TOLERANCE_SECONDS", EMAIL_CFG_TEXT)
        self.assertIn("'OTP_AFTER_TS_TOLERANCE_SECONDS': 'int'", EMAIL_CFG_TEXT)

    def test_mail_client_no_longer_uses_hardcoded_two_second_window(self):
        self.assertNotIn("msg_ts + 2 < after_ts", MAIL_TEXT)
        self.assertNotIn("msg_ts + 30 < after_ts", MAIL_TEXT)

    def test_every_staleness_filter_uses_shared_tolerance(self):
        self.assertEqual(MAIL_TEXT.count("msg_ts + _after_ts_tolerance() < after_ts"), 5)


class CodexOtpBaselineTests(unittest.TestCase):
    def test_baseline_is_email_submit_time_not_page_arrival_time(self):
        body = _function_source(TARGET, TEXT, "_fill_email_and_otp")
        # 提交邮箱前记录基准
        submit_ts_at = body.index("email_submit_ts = time.time()")
        submit_at = body.index("_submit_email_step(driver)", submit_ts_at)
        self.assertLess(submit_ts_at, submit_at)
        self.assertIn("otp_after_ts = email_submit_ts", body)

    def test_email_otp_branch_does_not_reset_baseline_to_now(self):
        body = _function_source(TARGET, TEXT, "_fill_email_and_otp")
        branch_at = body.index('if pw_result == "email_otp":')
        next_at = body.index('if pw_result == "next_step":', branch_at)
        branch = body[branch_at:next_at]
        # 到达验证码页时不能把基准前移到"现在"，否则会丢掉刚发出的验证码
        self.assertNotIn("otp_after_ts = time.time()", branch)
        self.assertIn("otp_after_ts = email_submit_ts", branch)

    def test_recovery_path_also_records_submit_time(self):
        body = _function_source(TARGET, TEXT, "_fill_email_and_otp")
        recovery_at = body.index("def _recover_email_submit_stall_once()")
        recovery_end = body.index('logger.info("[Codex][Browser] 打开授权地址")', recovery_at)
        recovery = body[recovery_at:recovery_end]
        self.assertIn("email_submit_ts = time.time()", recovery)
        self.assertIn("otp_after_ts = email_submit_ts", recovery)
        # 恢复路径不能再用"到达验证码页"的时刻覆盖基准
        self.assertNotIn("otp_after_ts = time.time()", recovery)


class RestartFlowReturnValueTests(unittest.TestCase):
    def test_restart_helper_returns_status(self):
        body = _function_source(TARGET, TEXT, "_fill_email_and_otp")
        restart_at = body.index("def _restart_email_otp_flow(")
        restart_end = body.index("for otp_attempt in range(", restart_at)
        restart = body[restart_at:restart_end]
        self.assertIn('return "logged_in"', restart)
        self.assertIn('return "email_otp"', restart)
        # 密码登录成功分支必须返回状态，而不是被外层忽略
        self.assertIn('return "logged_in"', restart)

    def test_outer_loop_consumes_logged_in_and_stops_polling(self):
        body = _function_source(TARGET, TEXT, "_fill_email_and_otp")
        loop_at = body.index("for otp_attempt in range(")
        loop = body[loop_at:]
        call_at = loop.index("_restart_email_otp_flow(")
        call_end = loop.index("continue", call_at)
        call = loop[call_at:call_end]
        self.assertIn('== "logged_in"', call)
        self.assertIn("return", call)


class AfterTsToleranceBehaviourTests(unittest.TestCase):
    """用 Job 411 的真实时间线验证容差行为。"""

    # 邮件服务端时间：2026-10-14T20:57:07+08:00 == UTC 12:57:07
    MAIL_TS = 1791982627.0
    # codex 轮询基准（页面到达验证码页）：12:58:06 UTC
    POLL_AFTER_TS = 1791982686.0

    def test_job411_mail_survives_wide_tolerance(self):
        tolerance = _load_tolerance_helper()()
        # 旧实现 30s 容差：12:57:07 + 30 < 12:58:06 -> 被丢弃（真实故障）
        self.assertTrue(self.MAIL_TS + 30 < self.POLL_AFTER_TS)
        # 新容差：不再被当成旧码
        self.assertFalse(self.MAIL_TS + tolerance < self.POLL_AFTER_TS)

    def test_ancient_code_is_still_filtered(self):
        tolerance = _load_tolerance_helper()()
        ancient = self.POLL_AFTER_TS - 3600
        self.assertTrue(ancient + tolerance < self.POLL_AFTER_TS)


class OtpLeadingZeroInputTests(unittest.TestCase):
    """验证码可能带前导零（Job 415 是 012878）：输入后必须回读校验。

    只调用 send_keys 而不回读，会把"输入未生效或被浏览器规范化"误判成
    "验证码错误"，于是重发换码，直到把重试预算耗尽。
    """

    def test_otp_value_reader_exists(self):
        self.assertIn("def _otp_input_value(", ROXY_REG_TEXT)
        self.assertIn("def _sync_otp_inputs(", ROXY_REG_TEXT)

    def test_single_input_path_verifies_after_typing(self):
        body = _function_source(ROXY_REG, ROXY_REG_TEXT, "_type_otp")
        typed_at = body.index("_human_type_text(driver, els[0], code, clear=True)")
        after = body[typed_at:typed_at + 400]
        self.assertIn("_sync_otp_inputs(driver, [els[0]], code)", after)

    def test_multi_box_path_verifies_after_typing(self):
        body = _function_source(ROXY_REG, ROXY_REG_TEXT, "_type_otp")
        loop_at = body.index("for e, ch in zip(numeric_boxes, code):")
        after = body[loop_at:]
        self.assertIn("_sync_otp_inputs(driver, numeric_boxes[: len(code)], code)", after)

    def test_sync_rewrites_with_native_setter_when_mismatched(self):
        body = _function_source(ROXY_REG, ROXY_REG_TEXT, "_sync_otp_inputs")
        self.assertIn('actual == code', body)
        self.assertIn("_set_element_value(driver, el, value)", body)
        # 不一致时必须留下可诊断日志，而不是静默提交
        self.assertIn("实际值与验证码不一致", body)

    def test_leading_zero_code_is_preserved_verbatim(self):
        # 拼接回读值时不做过任何 int()/strip() 转换，前导零必须原样保留
        body = _function_source(ROXY_REG, ROXY_REG_TEXT, "_sync_otp_inputs")
        self.assertNotIn("int(", body)
        self.assertNotIn("lstrip", body)
        self.assertIn('code = str(code or "")', body)


class ExcludedCodeReuseTests(unittest.TestCase):
    """服务端复用同一个验证码时（Job 415 连续三封都是 012878）不能永久排除它。

    严格排除会把本轮唯一可用的码拉黑，只能按 3s 间隔空转到整轮超时。
    """

    def test_reuse_config_and_helper_exist(self):
        self.assertIn("OTP_EXCLUDED_CODE_REUSE_SECONDS", EMAIL_CFG_TEXT)
        self.assertIn("'OTP_EXCLUDED_CODE_REUSE_SECONDS': 'int'", EMAIL_CFG_TEXT)
        self.assertIn("_OTP_EXCLUDED_REUSE_AFTER_SECONDS = 60", MAIL_TEXT)
        self.assertIn("_excluded_reuse_after_seconds()", MAIL_TEXT)

    def test_poll_loop_holds_then_reuses_excluded_code(self):
        body = _function_source(MAIL_CLIENT, MAIL_TEXT, "fetch_latest_otp")
        self.assertIn("excluded_only_since", body)
        self.assertIn("excluded_hold", body)
        # 命中排除集时先记录持有值并计时，而不是直接丢弃
        self.assertIn("excluded_hold = excluded_hold or code", body)
        self.assertIn("excluded_only_since = excluded_only_since or time.time()", body)
        # 出现新码时清零
        self.assertIn("excluded_only_since = None", body)
        self.assertIn('excluded_hold = ""', body)
        # 超窗口且无新码时返回持有的码，而不是抛错
        self.assertIn("_excluded_reuse_after_seconds()", body)
        self.assertIn("return excluded_hold", body)
        self.assertIn("复用已提交过的验证码作为最终候选", body)

    def test_reuse_helper_default_is_bounded(self):
        tree = ast.parse(MAIL_TEXT, filename=str(MAIL_CLIENT))
        body = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_excluded_reuse_after_seconds"
        ]
        self.assertTrue(body)
        namespace = {
            "_email_cfg": type("Cfg", (), {"OTP_EXCLUDED_CODE_REUSE_SECONDS": 60})(),
            "_OTP_EXCLUDED_REUSE_AFTER_SECONDS": 60,
        }
        exec(compile(ast.Module(body=body, type_ignores=[]), str(MAIL_CLIENT), "exec"), namespace)
        self.assertGreater(namespace["_excluded_reuse_after_seconds"](), 0)
        self.assertLessEqual(namespace["_excluded_reuse_after_seconds"](), 180)
        # 0 表示关闭回退复用，helper 必须原样返回 0
        namespace["_email_cfg"] = type("Cfg", (), {"OTP_EXCLUDED_CODE_REUSE_SECONDS": 0})()
        self.assertEqual(namespace["_excluded_reuse_after_seconds"](), 0)


if __name__ == "__main__":
    unittest.main()
