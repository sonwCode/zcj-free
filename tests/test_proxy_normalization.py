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


if __name__ == "__main__":
    unittest.main()
