# -*- coding: utf-8 -*-
import logging
import sys
import threading
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from core import session as session_mod
from core import cloakbrowser_registration as cloak_registration
from core.browser_use_client import BrowserUseClient
from core.cloakbrowser_driver import CloakSeleniumDriver, build_cloak_driver
from core.cloud_browser_lifecycle import CloudBrowserLease
from core.roxybrowser_client import RoxyBrowserClient
from core.skyvern_client import SkyvernClient
from core.stop_control import StopRequested


class _ProtocolSession:
    created = []

    def __init__(self, *args, **kwargs):
        self.proxy = ""
        self.device_id = "device-id"
        self.oai_session_id = "oai-session"
        self.auth_session_logging_id = "auth-session"
        self.close_calls = 0
        self.__class__.created.append(self)

    @staticmethod
    def _short_value(value, limit):
        return str(value or "")[:limit]

    def fingerprint_summary(self):
        return {
            "user_agent": "ua",
            "accept_language": "en-US",
            "timezone_iana": "UTC",
            "timezone_offset_minutes": 0,
            "screen_width": 1280,
            "screen_height": 720,
            "device_pixel_ratio": 1,
            "hardware_concurrency": 4,
            "device_memory": 8,
            "geo_country": "US",
            "geo_city": "Test",
        }

    def close(self):
        self.close_calls += 1


class _Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = str(data)

    def json(self):
        return self._data


class DriverLifecycleTests(unittest.TestCase):
    def setUp(self):
        _ProtocolSession.created = []

    def test_cloak_cleanup_watchdog_unblocks_stuck_callback(self):
        release = threading.Event()
        timed_out = threading.Event()

        def callback():
            release.wait(1.0)
            return "released"

        def force_kill():
            timed_out.set()
            release.set()

        result = cloak_registration._bounded_cleanup(
            "traffic_tracker.stop",
            callback,
            on_timeout=force_kill,
            timeout_seconds=0.01,
        )

        self.assertEqual(result, "released")
        self.assertTrue(timed_out.is_set())

    def test_protocol_registration_closes_session_after_error(self):
        with patch.object(main._roxy_cfg, "REGISTRATION_DRIVER", "protocol"), \
             patch.object(main, "BrowserSession", _ProtocolSession), \
             patch.object(main, "network_preflight", side_effect=RuntimeError("boom")):
            result = main.run_registration("user@example.test", "User", "1990-01-01")

        self.assertFalse(result["success"])
        self.assertEqual(_ProtocolSession.created[0].close_calls, 1)

    def test_protocol_registration_closes_session_after_stop(self):
        with patch.object(main._roxy_cfg, "REGISTRATION_DRIVER", "protocol"), \
             patch.object(main, "BrowserSession", _ProtocolSession), \
             patch.object(main, "network_preflight", side_effect=StopRequested()):
            with self.assertRaises(StopRequested):
                main.run_registration("user@example.test", "User", "1990-01-01")

        self.assertEqual(_ProtocolSession.created[0].close_calls, 1)

    def test_browser_session_constructor_rolls_back_partial_resources(self):
        raw = Mock()
        relay = Mock()
        with patch.object(session_mod, "pick_proxy", return_value="socks5://proxy.example:1080"), \
             patch("core.proxy_chain.open_proxy_pool_proxy", return_value=("socks5://127.0.0.1:12345", relay)), \
             patch.object(session_mod, "Session", return_value=raw), \
             patch.object(session_mod.BrowserSession, "_detect_exit_geo", side_effect=RuntimeError("geo failed")):
            with self.assertRaisesRegex(RuntimeError, "geo failed"):
                session_mod.BrowserSession()

        raw.close.assert_called_once_with()
        relay.close.assert_called_once_with()

    def test_browser_session_close_is_idempotent(self):
        raw = Mock()
        relay = Mock()
        browser_session = object.__new__(session_mod.BrowserSession)
        browser_session.session = raw
        browser_session._proxy_pool_relay = relay
        browser_session._closed = False

        browser_session.close()
        browser_session.close()

        raw.close.assert_called_once_with()
        relay.close.assert_called_once_with()

    def test_roxy_open_failure_rolls_back_created_profile(self):
        client = RoxyBrowserClient(api_base="http://127.0.0.1:50000", token="")
        with patch.object(client, "create_profile", return_value="profile-1"), \
             patch.object(client, "_open_known_profile", side_effect=RuntimeError("open failed")), \
             patch.object(client, "close_profile") as close_profile, \
             patch.object(client, "delete_profile") as delete_profile, \
             patch.object(client, "close_proxy_pool_relay") as close_relay:
            with self.assertRaisesRegex(RuntimeError, "open failed"):
                client._open_profile_locked("")

        close_profile.assert_called_once_with("profile-1", timeout_seconds=5.0, max_attempts=1)
        delete_profile.assert_called_once_with("profile-1", timeout_seconds=5.0, max_attempts=1)
        close_relay.assert_called_once_with()
        client.close()

    def test_cloak_quit_deduplicates_persistent_context(self):
        shared = Mock()
        relay = Mock()
        driver = CloakSeleniumDriver(shared, shared, Mock(), proxy_relay=relay)

        driver.quit()
        driver.quit()

        shared.close.assert_called_once_with()
        relay.close.assert_called_once_with()

    def test_cloak_builder_rolls_back_context_when_page_creation_fails(self):
        context = Mock()
        context.new_page.side_effect = RuntimeError("page failed")
        browser = Mock()
        browser.new_context.return_value = context
        fake_module = types.SimpleNamespace(
            launch=Mock(return_value=browser),
            launch_persistent_context=Mock(),
        )
        with patch.dict(sys.modules, {"cloakbrowser": fake_module}), \
             patch("core.cloakbrowser_driver._build_cloak_locale_options", return_value={}), \
             patch("core.cloakbrowser_driver._cfg.CLOAK_USE_PROXY", False), \
             patch("core.cloakbrowser_driver._cfg.CLOAK_USER_DATA_DIR", ""):
            with self.assertRaisesRegex(RuntimeError, "page failed"):
                build_cloak_driver(proxy="")

        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()

    def test_cloak_close_error_forces_process_cleanup(self):
        browser = Mock()
        context = Mock()
        context.close.side_effect = RuntimeError("close failed")
        driver = CloakSeleniumDriver(browser, context, Mock())
        with patch.object(driver, "force_kill") as force_kill:
            from core.cloakbrowser_driver import close_cloak_driver
            self.assertFalse(close_cloak_driver(driver))

        force_kill.assert_called_once_with()

    def test_cloud_lease_closes_browser_before_playwright_exit(self):
        events = []

        class _Manager:
            def __enter__(self):
                events.append("playwright-enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                events.append("playwright-exit")

        browser = Mock()
        browser.close.side_effect = lambda: events.append("browser")
        lease = CloudBrowserLease(
            Mock(),
            SimpleNamespace(session_id=""),
            provider_label="Cloud",
            keep_open=False,
            logger=logging.getLogger(__name__),
        )
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(_Manager())
            stack.callback(lease.close)
            lease.attach_browser(browser)

        self.assertEqual(events, ["playwright-enter", "browser", "playwright-exit"])

    def test_cloud_watchdog_stops_remote_when_cdp_close_blocks(self):
        release = threading.Event()
        browser = Mock()
        browser.close.side_effect = lambda: release.wait(1)
        client = Mock()
        client.close_browser_session.side_effect = lambda session_id: release.set()
        lease = CloudBrowserLease(
            client,
            SimpleNamespace(session_id="session-watchdog"),
            provider_label="Cloud",
            keep_open=False,
            logger=logging.getLogger(__name__),
        )
        lease.attach_browser(browser)

        self.assertTrue(lease.close_browser(timeout_seconds=0.01))
        client.close_browser_session.assert_called_once_with("session-watchdog")

    def test_cloud_lease_closes_each_resource_once(self):
        events = []
        browser = Mock()
        browser.close.side_effect = lambda: events.append("browser")
        client = Mock()
        client.close_browser_session.side_effect = lambda session_id: events.append(f"remote:{session_id}")
        lease = CloudBrowserLease(
            client,
            SimpleNamespace(session_id="session-1"),
            provider_label="Cloud",
            keep_open=False,
            logger=logging.getLogger(__name__),
        )
        lease.attach_browser(browser)

        lease.close()
        lease.close()

        self.assertEqual(events, ["remote:session-1", "browser"])

    def test_cloud_lease_retries_remote_stop_before_marking_closed(self):
        client = Mock()
        client.close_browser_session.side_effect = [RuntimeError("temporary"), {"ok": True}]
        lease = CloudBrowserLease(
            client,
            SimpleNamespace(session_id="session-retry"),
            provider_label="Cloud",
            keep_open=False,
            logger=logging.getLogger(__name__),
        )

        self.assertTrue(lease.close_remote())
        self.assertTrue(lease.close_remote())
        self.assertEqual(client.close_browser_session.call_count, 2)

    def test_cloud_lease_rolls_back_unconnected_session_even_when_keep_open(self):
        client = Mock()
        lease = CloudBrowserLease(
            client,
            SimpleNamespace(session_id="session-2"),
            provider_label="Cloud",
            keep_open=True,
            logger=logging.getLogger(__name__),
        )

        lease.close_remote()

        client.close_browser_session.assert_called_once_with("session-2")

    @patch("core.browser_use_client._cfg.BROWSER_USE_CONNECT_MODE", "api_v4")
    @patch("core.browser_use_client._cfg.BROWSER_USE_PROFILE_ID", "")
    @patch("core.browser_use_client._cfg.BROWSER_USE_USE_PROXY", True)
    @patch("core.browser_use_client._cfg.BROWSER_USE_PROXY_COUNTRY_CODE", "jp")
    @patch("core.browser_use_client._cfg.BROWSER_USE_API_KEY", "key-123")
    def test_browser_use_v4_session_is_explicitly_stopped_once(self):
        create = _Response({"id": "browser-1", "cdpUrl": "wss://example.test/cdp"})
        stop = _Response({"id": "browser-1", "status": "stopped"})
        with patch("core.browser_use_client.requests.post", return_value=create) as post, \
             patch("core.browser_use_client.requests.patch", return_value=stop) as patch_request:
            client = BrowserUseClient()
            session_info = client.open_session()
            client.close_browser_session(session_info.session_id)
            client.close_browser_session(session_info.session_id)

        self.assertEqual(session_info.session_id, "browser-1")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(patch_request.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://api.browser-use.com/api/v4/browsers")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Browser-Use-API-Key"], "key-123")
        self.assertEqual(post.call_args.kwargs["json"]["proxyCountryCode"], "jp")
        self.assertEqual(patch_request.call_args.args[0], "https://api.browser-use.com/api/v4/browsers/browser-1")
        self.assertEqual(patch_request.call_args.kwargs["json"], {"action": "stop"})

    @patch("core.browser_use_client._cfg.BROWSER_USE_API_KEY", "key-123")
    def test_browser_use_stop_can_retry_after_failure(self):
        failed = _Response({"error": "temporary"}, status_code=503)
        stopped = _Response({"status": "stopped"})
        with patch("core.browser_use_client.requests.patch", side_effect=[failed, stopped]) as patch_request:
            client = BrowserUseClient()
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                client.close_browser_session("browser-retry")
            client.close_browser_session("browser-retry")

        self.assertEqual(patch_request.call_count, 2)

    def test_browser_use_connect_mode_rejects_unknown_value(self):
        client = BrowserUseClient(api_key="key-123")
        with patch("core.browser_use_client._cfg.BROWSER_USE_CONNECT_MODE", "typo"):
            with self.assertRaisesRegex(RuntimeError, "BROWSER_USE_CONNECT_MODE"):
                client.open_session()

    def test_skyvern_open_failure_closes_created_session(self):
        client = SkyvernClient(api_key="key-123", api_base="https://api.example.test")
        with patch.object(client, "create_browser_session", return_value={"browser_session_id": "sky-1"}), \
             patch.object(client, "get_browser_session", side_effect=RuntimeError("poll failed")), \
             patch.object(client, "close_browser_session", return_value={"ok": True}) as close_session, \
             patch("core.skyvern_client._stop_sleep"):
            with self.assertRaisesRegex(RuntimeError, "poll failed"):
                client.open_session()

        close_session.assert_called_once_with("sky-1")

    def test_roxy_rollback_keeps_original_error_when_cleanup_fails(self):
        client = RoxyBrowserClient(api_base="http://127.0.0.1:50000", token="")
        with patch.object(client, "create_profile", return_value="profile-2"), \
             patch.object(client, "_open_known_profile", side_effect=RuntimeError("open failed")), \
             patch.object(client, "close_profile", side_effect=RuntimeError("close failed")), \
             patch.object(client, "delete_profile", side_effect=RuntimeError("delete failed")), \
             patch.object(client, "close_proxy_pool_relay") as close_relay:
            with self.assertRaisesRegex(RuntimeError, "open failed"):
                client._open_profile_locked("")

        close_relay.assert_called_once_with()
        client.close()


if __name__ == "__main__":
    unittest.main()
