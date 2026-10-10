# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import twofa_service
from core.account_export import AccountUnusableError, TwofaReauthTransientError


class TwofaServiceSettingsTests(unittest.TestCase):
    def test_executor_uses_configured_worker_count(self):
        settings = twofa_service.queue_settings()

        self.assertGreaterEqual(settings["workers"], 1)
        self.assertLessEqual(settings["workers"], 16)
        self.assertGreaterEqual(settings["queue_limit"], settings["workers"])
        self.assertEqual(twofa_service._EXECUTOR._max_workers, settings["workers"])

    def test_integer_settings_are_bounded(self):
        original = getattr(twofa_service._twofa_cfg, "TWOFA_WORKERS", None)
        try:
            twofa_service._twofa_cfg.TWOFA_WORKERS = 999
            self.assertEqual(twofa_service._int_setting("TWOFA_WORKERS", 4, 1, 16), 16)
            twofa_service._twofa_cfg.TWOFA_WORKERS = -5
            self.assertEqual(twofa_service._int_setting("TWOFA_WORKERS", 4, 1, 16), 1)
        finally:
            twofa_service._twofa_cfg.TWOFA_WORKERS = original

    def test_fallback_selector_prefers_dedicated_twofa_pool(self):
        fallback = "http://fallback-user:fallback-pass@trustsource.test:10000"
        with patch.object(twofa_service._twofa_cfg, "TWOFA_PROXY_MODE", "saved"), patch.object(
            twofa_service._twofa_cfg, "TWOFA_PROXY_FALLBACK_POOL", [fallback]
        ), patch(
            "core.proxy_chain.open_proxy_pool_proxy", return_value=(fallback, None)
        ) as open_proxy:
            transport, relay, source = twofa_service._resolve_twofa_proxy(
                None, excluded_targets={"http://failed.test:1"}, force_fallback_pool=True
            )

        self.assertEqual(transport, fallback)
        self.assertIsNone(relay)
        self.assertEqual(source, "fallback_pool")
        open_proxy.assert_called_once_with(fallback)

    def test_fallback_selector_prefers_different_proxy_host(self):
        first = "http://first-user:first-pass@trustsource.test:10000"
        second = "http://second-user:second-pass@other-proxy.test:10000"
        with patch.object(twofa_service._twofa_cfg, "TWOFA_PROXY_FALLBACK_POOL", [first, second]), patch(
            "core.proxy_chain.open_proxy_pool_proxy", side_effect=lambda target: (target, None)
        ) as open_proxy:
            transport, relay, source = twofa_service._resolve_twofa_proxy(
                None, excluded_targets={first}, force_fallback_pool=True
            )

        self.assertEqual(transport, second)
        self.assertIsNone(relay)
        self.assertEqual(source, "fallback_pool")
        open_proxy.assert_called_once_with(second)

    def test_account_unusable_error_is_persisted_without_fallback(self):
        session = Mock()
        session.proxy = "saved-proxy"
        session.proxy_target = "saved-proxy"
        session.device_id = "device-id"
        session.fingerprint_summary_text.return_value = "test-fingerprint"
        updates = []
        resolve_calls = []

        def resolve(proxy, *, excluded_targets=None, force_fallback_pool=False):
            resolve_calls.append((proxy, set(excluded_targets or ()), force_fallback_pool))
            return ("saved-proxy", None, "saved")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            twofa_service, "log_path", side_effect=lambda email: Path(tmp) / "twofa.log"
        ), patch.object(twofa_service.logging, "FileHandler", return_value=Mock()), patch.object(
            twofa_service.logging, "getLogger", return_value=Mock()
        ), patch.object(twofa_service, "_resolve_twofa_proxy", side_effect=resolve), patch.object(
            twofa_service, "BrowserSession", return_value=session
        ), patch.object(
            twofa_service, "setup_2fa",
            side_effect=AccountUnusableError("account deactivated", error_code="account_deactivated"),
        ), patch.object(twofa_service, "close_browser_session"), patch.object(
            twofa_service, "_append_log"
        ), patch.object(twofa_service.db, "mark_account_totp_setup_running", return_value=True), patch.object(
            twofa_service.db,
            "update_account_totp_secret",
            side_effect=lambda *args, **kwargs: updates.append((args, kwargs)),
        ):
            result = twofa_service._run_twofa(
                account_id=8,
                email="user@example.com",
                access_token="access-token",
                proxy="saved-proxy",
                trigger="test-deactivated",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "account_deactivated")
        self.assertEqual(len(resolve_calls), 1)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][0][1]["error_code"], "account_deactivated")

    def test_saved_proxy_initial_403_rotates_once_to_pool_session(self):
        class _Session:
            def __init__(self, proxy, fingerprint_seed):
                self.proxy = proxy
                self.proxy_target = proxy
                self.device_id = fingerprint_seed
                self.closed = False

            def fingerprint_summary_text(self):
                return "test-fingerprint"

            def close(self):
                self.closed = True

        first = _Session("saved-proxy", "first")
        second = _Session("pool-proxy", "second")
        setup_calls = []
        resolve_calls = []
        updates = []

        def resolve(proxy, *, excluded_targets=None, force_fallback_pool=False):
            resolve_calls.append((proxy, set(excluded_targets or ()), force_fallback_pool))
            return ("saved-proxy", None, "saved") if len(resolve_calls) == 1 else ("pool-proxy", None, "fallback_pool")

        setup_exclusions = []

        def setup(session, email, access_token, exclude_otp_codes=None):
            setup_calls.append(session)
            setup_exclusions.append(exclude_otp_codes)
            if len(setup_calls) == 1:
                exclude_otp_codes.add("old-code")
                raise TwofaReauthTransientError("otp validate 403", stage="otp_validate")
            self.assertIn("old-code", exclude_otp_codes)
            return "JBSWY3DPEHPK3PXP"

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            twofa_service, "log_path", side_effect=lambda email: Path(tmp) / "twofa.log"
        ), patch.object(twofa_service.logging, "FileHandler", return_value=Mock()), patch.object(
            twofa_service.logging, "getLogger", return_value=Mock()
        ), patch.object(twofa_service, "_resolve_twofa_proxy", side_effect=resolve), patch.object(
            twofa_service, "BrowserSession", side_effect=[first, second]
        ), patch.object(twofa_service, "setup_2fa", side_effect=setup), patch.object(
            twofa_service, "close_browser_session", side_effect=lambda session: session.close()
        ), patch.object(twofa_service, "_append_log"), patch.object(
            twofa_service.db, "mark_account_totp_setup_running", return_value=True
        ), patch.object(
            twofa_service.db, "update_account_totp_secret", side_effect=lambda *args, **kwargs: updates.append((args, kwargs))
        ):
            twofa_service._twofa_cfg.TWOFA_REAUTH_PROXY_FALLBACK = True
            twofa_service._twofa_cfg.TWOFA_REAUTH_PROXY_FALLBACK_ATTEMPTS = 1
            result = twofa_service._run_twofa(
                account_id=7,
                email="user@example.com",
                access_token="access-token",
                proxy="saved-proxy",
                trigger="test",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(setup_calls), 2)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(resolve_calls[1][0], None)
        self.assertIn("saved-proxy", resolve_calls[1][1])
        self.assertTrue(resolve_calls[1][2])
        self.assertEqual(len(updates), 1)


if __name__ == "__main__":
    unittest.main()
