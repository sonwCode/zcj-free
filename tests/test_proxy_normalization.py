# -*- coding: utf-8 -*-
import unittest

from unittest.mock import patch

from config import proxy as proxy_config
from config.proxy import normalize_proxy_list, normalize_proxy_url


class ProxyNormalizationTests(unittest.TestCase):
    def test_host_port_gets_default_scheme(self):
        self.assertEqual(
            normalize_proxy_url("127.0.0.1:7897"),
            "http://127.0.0.1:7897",
        )

    def test_host_port_username_password_format_encodes_credentials(self):
        self.assertEqual(
            normalize_proxy_url("proxy.example.test:8080:user:p@ ss"),
            "http://user:p%40%20ss@proxy.example.test:8080",
        )

    def test_username_password_host_port_format_encodes_credentials(self):
        self.assertEqual(
            normalize_proxy_url("user:p@ ss:proxy.example.test:8080"),
            "http://user:p%40%20ss@proxy.example.test:8080",
        )

    def test_existing_scheme_is_preserved(self):
        self.assertEqual(
            normalize_proxy_url("socks5h://127.0.0.1:7897"),
            "socks5h://127.0.0.1:7897",
        )

    def test_invalid_port_is_preserved(self):
        self.assertEqual(
            normalize_proxy_url("proxy.example.test:not-a-port"),
            "proxy.example.test:not-a-port",
        )

    def test_list_filters_blank_entries(self):
        self.assertEqual(
            normalize_proxy_list(["", "  ", "127.0.0.1:7897"]),
            ["http://127.0.0.1:7897"],
        )

    def test_proxy_leases_spread_concurrent_browsers(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ):
            proxy_config._reset_proxy_leases_for_tests()
            first = proxy_config.acquire_proxy_lease()
            second = proxy_config.acquire_proxy_lease()
            self.assertNotEqual(first, second)
            proxy_config.release_proxy_lease(first)
            proxy_config.release_proxy_lease(second)
            self.assertEqual(proxy_config._PROXY_LEASES, {})

    def test_pick_proxy_excluding_avoids_failed_target_when_alternative_exists(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ):
            self.assertEqual(
                proxy_config.pick_proxy_excluding({"http://proxy-a.test:1"}),
                "http://proxy-b.test:1",
            )


class ProxyCooldownTests(unittest.TestCase):
    """出口故障冷却：坏出口不能在同批任务里被反复选中。"""

    def setUp(self):
        proxy_config._reset_proxy_leases_for_tests()

    def tearDown(self):
        proxy_config._reset_proxy_leases_for_tests()

    def test_failed_proxy_is_deprioritized_in_lease_selection(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            selected = {proxy_config.acquire_proxy_lease() for _ in range(4)}
            self.assertNotIn("http://proxy-a.test:1", selected)
            self.assertIn("http://proxy-b.test:1", selected)

    def test_failed_proxy_is_deprioritized_in_excluding_pick(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            self.assertEqual(
                proxy_config.pick_proxy_excluding(),
                "http://proxy-b.test:1",
            )

    def test_all_cooling_proxies_still_fall_back_to_pool(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1"],
        ):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            # 唯一出口在冷却中时仍必须可用，否则会直接无代理可用。
            self.assertEqual(proxy_config.acquire_proxy_lease(), "http://proxy-a.test:1")

    def test_success_clears_cooldown(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            proxy_config.note_proxy_success("http://proxy-a.test:1")
            self.assertEqual(proxy_config._cooling_proxies(), set())

    def test_zero_cooldown_disables_marking(self):
        with patch.object(proxy_config, "PROXY_FAILURE_COOLDOWN_SECONDS", 0):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            self.assertEqual(proxy_config._cooling_proxies(), set())

    def test_cooldown_expires(self):
        with patch.object(
            proxy_config,
            "PROXY_POOL",
            ["http://proxy-a.test:1", "http://proxy-b.test:1"],
        ), patch.object(proxy_config, "PROXY_FAILURE_COOLDOWN_SECONDS", 60):
            proxy_config.note_proxy_failure("http://proxy-a.test:1")
            self.assertIn("http://proxy-a.test:1", proxy_config._cooling_proxies())
            # 冷却到期后应重新参与选择。
            with patch.object(proxy_config.time, "monotonic", return_value=proxy_config.time.monotonic() + 120):
                self.assertEqual(proxy_config._cooling_proxies(), set())


if __name__ == "__main__":
    unittest.main()
