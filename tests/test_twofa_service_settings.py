# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import twofa_service
from core.account_export import TwofaReauthTransientError


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

        def resolve(proxy, *, excluded_targets=None):
            resolve_calls.append((proxy, set(excluded_targets or ())))
            return ("saved-proxy", None, "saved") if len(resolve_calls) == 1 else ("pool-proxy", None, "pool")

        def setup(session, email, access_token):
            setup_calls.append(session)
            if len(setup_calls) == 1:
                raise TwofaReauthTransientError("initial csrf 403", stage="initial")
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
        self.assertEqual(len(updates), 1)


if __name__ == "__main__":
    unittest.main()
