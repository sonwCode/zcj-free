# -*- coding: utf-8 -*-
import ast
from io import BytesIO
import importlib.util
import inspect
import json
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
STAGED_PROVIDER = PROJECT / "core" / "sms_provider.py"
ROXY_SOURCE = PROJECT / "core" / "roxy_codex_oauth.py"
BROWSER_USE_SOURCE = PROJECT / "core" / "browser_use_codex_oauth.py"
STAGED_CODEX = PROJECT / "config" / "codex.py"


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return json.loads(self.text)


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, params=None, **kwargs):
        self.calls.append({"method": "GET", "url": url, "params": dict(params or {})})
        return self.responses.pop(0)

    def post(self, url, headers=None, data=None, **kwargs):
        self.calls.append({"method": "POST", "url": url, "headers": headers or {}, "data": data})
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _load_staged_provider():
    sentinel = object()
    module_names = (
        "curl_cffi",
        "curl_cffi.requests",
        "config",
        "core",
        "core.registration_service",
        "core.stop_control",
        "staged_sms_provider",
    )
    previous = {name: sys.modules.get(name, sentinel) for name in module_names}
    try:
        curl_cffi = types.ModuleType("curl_cffi")
        requests = types.ModuleType("curl_cffi.requests")
        requests.Session = _Http
        curl_cffi.requests = requests
        sys.modules["curl_cffi"] = curl_cffi
        sys.modules["curl_cffi.requests"] = requests

        codex = types.SimpleNamespace(
            SMS_PROVIDER="smsbower",
            SMS_SERVICE="dr",
            SMS_COUNTRY="10",
            SMS_REQUEST_TIMEOUT=5,
            SMS_FX_RATE_URL="",
            SMS_FX_RATE_TTL=600,
            SMS_FX_CACHE_FILE="",
            TIGER_SMS_API_KEY="tiger-key",
            TIGER_SMS_API_BASE="http://tiger.test/stubs/handler_api.php",
            TIGER_SMS_USE_V2=True,
            TIGER_SMS_PROVIDER_IDS="",
            TIGER_SMS_EXCEPT_PROVIDER_IDS="",
            TIGER_SMS_RANDOM_COUNTRY=True,
            TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS=12,
            SMS_CODE_WAIT=2,
            SMS_POLL_INTERVAL=0,
            SMS_MAX_PRICE="",
            SMSBOWER_API_KEY="test-key",
            SMSBOWER_API_BASE="http://sms.test/handler_api",
            SMSBOWER_USE_V2=True,
            SMSBOWER_MIN_PRICE="",
            SMSBOWER_USD_CNY_RATE="",
            SMSBOWER_PROVIDER_IDS="",
            SMSBOWER_EXCEPT_PROVIDER_IDS="",
            SMSBOWER_PHONE_EXCEPTION="",
            SMS_EXCEPT_REGIONS="",
            SMSBOWER_RANDOM_COUNTRY=True,
            SMSBOWER_RANDOM_COUNTRY_ATTEMPTS=12,
            HEROSMS_API_KEY="",
            HEROSMS_API_BASE="http://hero.test/stubs/handler_api.php",
            HEROSMS_USE_V2=False,
            HEROSMS_SERVICE="",
            HEROSMS_COUNTRY="",
            HEROSMS_MAX_PRICE="",
            HEROSMS_PROVIDER_IDS="",
            HEROSMS_EXCEPT_PROVIDER_IDS="",
            HEROSMS_PHONE_EXCEPTION="",
            SMSPOOL_API_KEY="",
            SMSPOOL_API_BASE="http://pool.test",
            SMSPOOL_SERVICE="",
            SMSPOOL_COUNTRY="",
            SMSPOOL_MAX_PRICE="",
            SMSPOOL_MIN_PRICE="",
            SMSPOOL_PRICING_OPTION="0",
            SMSPOOL_POOL="",
            SMS_NUMBER_ACQUIRE_RETRIES=3,
            SMS_NUMBER_REJECT_TTL=1800,
            SMS_COUNTRY_REJECT_TTL=900,
            SMS_TIER_FAILURE_THRESHOLD=2,
            SMS_TIER_COOLDOWN_SECONDS=2700,
        )
        config = types.ModuleType("config")
        config.codex = codex
        config.IMPERSONATE = "test"
        sys.modules["config"] = config

        core = types.ModuleType("core")
        core.__path__ = []
        registration_service = types.ModuleType("core.registration_service")
        registration_service.check_stop_requested = lambda: None
        core.registration_service = registration_service
        sys.modules["core"] = core
        sys.modules["core.registration_service"] = registration_service
        stop_control = types.ModuleType("core.stop_control")
        stop_control.sleep = lambda seconds, quantum=0.25: None
        core.stop_control = stop_control
        sys.modules["core.stop_control"] = stop_control

        spec = importlib.util.spec_from_file_location("staged_sms_provider", STAGED_PROVIDER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module, codex
    finally:
        for name, value in previous.items():
            if value is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


sms_provider, codex_config = _load_staged_provider()


class SmsProviderStrategyTests(unittest.TestCase):
    def test_provider_for_retry_rotates_configured_chain(self):
        codex_config.SMS_PROVIDER_CHAIN = "tiger,smsbower"
        self.assertEqual(sms_provider.provider_for_retry(1), "tiger")
        self.assertEqual(sms_provider.provider_for_retry(2), "smsbower")
        self.assertEqual(sms_provider.provider_for_retry(3), "tiger")

    def test_acquire_number_can_start_from_preferred_provider(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower,tiger"
        calls = []

        def acquire(provider, http, service=None, country=None):
            calls.append(provider)
            return "activation", "15550001111"

        with patch.object(sms_provider, "_acquire_number_for_provider", side_effect=acquire):
            result = sms_provider.acquire_number(http=_Http([]), preferred_provider="tiger")

        self.assertEqual(result, ("activation", "15550001111"))
        self.assertEqual(calls, ["tiger"])

    def setUp(self):
        sms_provider._reset_runtime_state_for_tests()
        codex_config.SMS_QUALITY_FILE = ""
        codex_config.SMS_QUALITY_MIN_SAMPLES = 3
        codex_config.SMS_PROVIDER = "smsbower"
        codex_config.SMS_PROVIDER_CHAIN = ""
        codex_config.SMS_RELEASE_RETRIES = 3
        codex_config.SMS_RELEASE_RETRY_DELAY = 0
        codex_config.SMS_SERVICE = "dr"
        codex_config.SMS_COUNTRY = "10"
        codex_config.SMS_FX_RATE_URL = ""
        codex_config.SMSBOWER_USE_V2 = True
        codex_config.TIGER_SMS_USE_V2 = True
        codex_config.TIGER_SMS_PROVIDER_IDS = ""
        codex_config.TIGER_SMS_EXCEPT_PROVIDER_IDS = ""
        codex_config.TIGER_SMS_RANDOM_COUNTRY = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMS_MAX_PRICE = ""
        codex_config.SMSBOWER_MIN_PRICE = ""
        codex_config.SMSBOWER_USD_CNY_RATE = ""
        # 回退汇率优先读最近已知值，测试必须重置，否则用例之间会互相污染。
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = "7.2"
        codex_config.SMS_FX_RATE_URLS = ""
        codex_config.TIGER_SMS_API_KEY = "tiger-key"
        codex_config.TIGER_SMS_API_BASE = "http://tiger.test/stubs/handler_api.php"
        codex_config.TIGER_SMS_USE_V2 = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMSBOWER_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMS_NUMBER_ACQUIRE_RETRIES = 3
        codex_config.SMS_PREFLIGHT_RETRIES = 1
        codex_config.SMS_PREFLIGHT_RETRY_DELAY = 0
        codex_config.SMSBOWER_EXCEPT_PROVIDER_IDS = ""
        codex_config.SMS_EXCEPT_REGIONS = ""
        codex_config.SMS_TIER_FAILURE_THRESHOLD = 2
        codex_config.SMS_TIER_COOLDOWN_SECONDS = 2700
        codex_config.SMS_COUNTRY_REJECT_TTL = 900
        codex_config.HEROSMS_API_KEY = ""
        codex_config.HEROSMS_API_BASE = "http://hero.test/stubs/handler_api.php"
        codex_config.HEROSMS_USE_V2 = False
        codex_config.HEROSMS_SERVICE = ""
        codex_config.HEROSMS_COUNTRY = ""
        codex_config.HEROSMS_MAX_PRICE = ""
        codex_config.HEROSMS_PROVIDER_IDS = ""
        codex_config.HEROSMS_EXCEPT_PROVIDER_IDS = ""
        codex_config.HEROSMS_PHONE_EXCEPTION = ""
        codex_config.SMSPOOL_API_KEY = ""
        codex_config.SMSPOOL_API_BASE = "http://pool.test"
        codex_config.SMSPOOL_SERVICE = ""
        codex_config.SMSPOOL_COUNTRY = ""
        codex_config.SMSPOOL_MAX_PRICE = ""
        codex_config.SMSPOOL_MIN_PRICE = ""
        codex_config.SMSPOOL_PRICING_OPTION = "0"
        codex_config.SMSPOOL_POOL = ""

    def test_smsbower_request_uses_configured_country_and_rmb_price(self):
        codex_config.SMS_COUNTRY = "35"
        codex_config.SMS_MAX_PRICE = "0.28"
        codex_config.SMSBOWER_RANDOM_COUNTRY = True

        params = sms_provider._smsbower_number_params("dr", None)

        self.assertEqual(params["country"], "35")
        self.assertEqual(params["service"], "dr")
        self.assertEqual(params["maxPrice"], "0.038888")

    def test_region_exclusion_matches_aliases_and_accents(self):
        codex_config.SMS_EXCEPT_REGIONS = "36/Quebec,Ontario"

        self.assertEqual(
            sms_provider._excluded_region({"phone_country_name": "Québec"}),
            "Québec",
        )
        self.assertEqual(
            sms_provider._excluded_region({"phone_country_name": "Ontario"}),
            "Ontario",
        )
        self.assertEqual(
            sms_provider._excluded_region({"phone_country_name": "Alberta", "phone_region": "CA"}),
            "",
        )

    def test_smsbower_excluded_region_is_released_then_retried(self):
        codex_config.SMSBOWER_RANDOM_COUNTRY = False
        codex_config.SMS_EXCEPT_REGIONS = "36/Quebec"
        codex_config.SMS_NUMBER_ACQUIRE_RETRIES = 2
        http = _Http([
            _Response(json.dumps({"activationId": "blocked-region", "phoneNumber": "15550001111", "countryCode": "36"})),
            _Response("ACCESS_CANCEL"),
            _Response(json.dumps({"activationId": "accepted-region", "phoneNumber": "15550002222", "countryCode": "36"})),
        ])
        regions = iter(("Quebec", "Ontario"))

        with patch.object(
            sms_provider,
            "_phone_location_metadata",
            side_effect=lambda phone: {"phone_country_name": next(regions)},
        ):
            activation_id, phone = sms_provider._acquire_number_for_provider(
                "smsbower", http, service="dr", country="36"
            )

        self.assertEqual((activation_id, phone), ("accepted-region", "15550002222"))
        self.assertEqual(
            [call["params"].get("action") for call in http.calls],
            ["getNumberV2", "setStatus", "getNumberV2"],
        )
        self.assertEqual(http.calls[1]["params"]["status"], "8")

    def test_excluded_region_filter_also_covers_other_providers(self):
        codex_config.SMS_EXCEPT_REGIONS = "Quebec"
        codex_config.SMS_NUMBER_ACQUIRE_RETRIES = 2
        states = iter(({"phone_country_name": "Quebec"}, {"phone_country_name": "Ontario"}))

        with patch.object(
            sms_provider, "_acquire_tiger_number",
            side_effect=(("blocked-region", "15550001111"), ("accepted-region", "15550002222")),
        ) as acquire, patch.object(
            sms_provider, "_activation_state", side_effect=lambda activation_id: next(states),
        ), patch.object(sms_provider, "_release_strategy_rejected") as release:
            result = sms_provider._acquire_number_for_provider("tiger", _Http([]), service="dr", country="36")

        self.assertEqual(result, ("accepted-region", "15550002222"))
        self.assertEqual(acquire.call_count, 2)
        release.assert_called_once_with("blocked-region", unittest.mock.ANY)

    def test_smsbower_blank_country_is_left_for_dynamic_inventory_selection(self):
        codex_config.SMS_COUNTRY = ""
        codex_config.SMS_MAX_PRICE = "0.28"

        params = sms_provider._smsbower_number_params("dr", None)

        self.assertNotIn("country", params)
        self.assertEqual(params["maxPrice"], "0.038888")

    def test_preflight_defers_stock_selection_to_provider_api(self):
        codex_config.SMS_COUNTRY = ""
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        http = _Http([])

        result = sms_provider.preflight_sms_dependency(http=http)

        self.assertEqual(result["probe"], "deferred_to_get_number")
        self.assertIsNone(result["available"])
        self.assertTrue(result["deferred"])
        self.assertIsNone(result["country"])
        self.assertEqual(http.calls, [])
        self.assertFalse(http.closed)

    def test_price_bounds_are_converted_from_rmb_before_request(self):
        codex_config.SMS_COUNTRY = "35"
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMSBOWER_MIN_PRICE = "0.86"
        codex_config.SMS_MAX_PRICE = "0.28"

        params = sms_provider._smsbower_number_params("dr", None)

        self.assertEqual(params["country"], "35")
        self.assertEqual(params["minPrice"], "0.119445")
        self.assertEqual(params["maxPrice"], "0.038888")

    def test_acquire_uses_dynamic_inventory_country_and_rmb_price_cap(self):
        codex_config.SMS_COUNTRY = ""
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMSBOWER_MIN_PRICE = ""
        codex_config.SMS_MAX_PRICE = "0.28"
        codex_config.SMSBOWER_USE_V2 = True
        http = _Http([
            _Response(json.dumps({"35": {"dr": {"cost": "0.02", "count": 3}}})),
            _Response(json.dumps({
                "activationId": "random-budget-1",
                "phoneNumber": "15550009999",
                "countryCode": "35",
                "activationCost": "0.02",
                "currency": "USD",
            })),
        ])

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda values: None):
            activation_id, phone = sms_provider.acquire_number(http=http)

        state = sms_provider.get_activation_info(activation_id)
        self.assertEqual((activation_id, phone), ("random-budget-1", "15550009999"))
        self.assertEqual(state["country"], "35")
        self.assertEqual([call["params"]["action"] for call in http.calls], ["getPrices", "getNumberV2"])
        self.assertEqual(http.calls[1]["params"]["country"], "35")
        self.assertEqual(http.calls[1]["params"]["maxPrice"], "0.038888")
        self.assertEqual(state["price_validated"], True)

    def test_dynamic_inventory_refreshes_after_temporary_no_candidate(self):
        codex_config.SMS_COUNTRY = ""
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMS_PREFLIGHT_RETRIES = 2
        codex_config.SMS_PREFLIGHT_RETRY_DELAY = 0
        high_snapshot = json.dumps({"117": {"dr": {"cost": "0.04", "count": 20}}})
        http = _Http(
            [_Response(high_snapshot) for _ in range(11)]
            + [
                _Response(json.dumps({"6": {"dr": {"cost": "0.023", "count": 20}}})),
                _Response(json.dumps({
                    "activationId": "refreshed-budget-1",
                    "phoneNumber": "15550006666",
                    "countryCode": "6",
                    "activationCost": "0.023",
                    "currency": "USD",
                })),
            ]
        )

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda values: None):
            activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("refreshed-budget-1", "15550006666"))
        self.assertEqual(
            [call["params"]["action"] for call in http.calls],
            ["getPrices"] * 12 + ["getNumberV2"],
        )
        self.assertEqual(http.calls[12]["params"]["country"], "6")
        self.assertEqual(http.calls[12]["params"]["maxPrice"], "0.034722")

    def test_stock_wait_seconds_extends_refresh_beyond_legacy_budget(self):
        codex_config.SMS_COUNTRY = ""
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMS_PREFLIGHT_RETRIES = 2
        codex_config.SMS_PREFLIGHT_RETRY_DELAY = 2
        codex_config.SMSBOWER_STOCK_WAIT_SECONDS = 90
        high_snapshot = json.dumps({"117": {"dr": {"cost": "0.04", "count": 20}}})

        class _PersistentHttp(_Http):
            def __init__(self):
                super().__init__([])

            def get(self, url, params=None, **kwargs):
                self.calls.append({"method": "GET", "url": url, "params": dict(params or {})})
                return _Response(high_snapshot)

        http = _PersistentHttp()

        with patch.object(sms_provider, "_stop_sleep"):
            with self.assertRaises(sms_provider.SmsNoNumbersError):
                sms_provider.acquire_number(http=http)

        # 旧实现固定 12 轮；按总等待时长应显著超过该下限。
        self.assertGreater(len(http.calls), 12)
        self.assertEqual({call["params"]["action"] for call in http.calls}, {"getPrices"})

    def test_preflight_never_queries_live_inventory(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower"
        codex_config.SMS_PREFLIGHT_RETRIES = 2
        codex_config.SMS_PREFLIGHT_RETRY_DELAY = 0
        http = _Http([])

        result = sms_provider.preflight_sms_dependency(http=http)

        self.assertTrue(result["deferred"])
        self.assertEqual(result["probe"], "deferred_to_get_number")
        self.assertEqual(http.calls, [])


    def test_unsupported_legacy_provider_falls_back_to_smsbower(self):
        codex_config.SMS_PROVIDER = "h"
        self.assertEqual(sms_provider._provider(), "smsbower")

    def test_provider_chain_is_ordered_deduplicated_and_explicit(self):
        codex_config.SMS_PROVIDER_CHAIN = "tiger,smsbower,tiger"
        self.assertEqual(sms_provider._provider_chain(), ["tiger", "smsbower"])
        self.assertEqual(sms_provider._attempt_provider_chain(), ["tiger", "smsbower"])

    def test_provider_configuration_and_balance_errors_do_not_fallback(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower,tiger"
        with patch.object(sms_provider, "_acquire_number_for_provider", side_effect=sms_provider.SmsNoBalanceError("empty")) as acquire:
            with self.assertRaises(sms_provider.SmsNoBalanceError):
                sms_provider.acquire_number(http=_Http([]), country="1")
        self.assertEqual(acquire.call_count, 1)

        with patch.object(sms_provider, "_acquire_number_for_provider", side_effect=sms_provider.SmsProviderConfigurationError("bad config")) as acquire:
            with self.assertRaises(sms_provider.SmsProviderConfigurationError):
                sms_provider.acquire_number(http=_Http([]), country="1")
        self.assertEqual(acquire.call_count, 1)

    def test_preflight_requires_a_configured_provider_chain(self):
        with patch.object(sms_provider, "_attempt_provider_chain", return_value=[]):
            with self.assertRaises(sms_provider.SmsProviderConfigurationError):
                sms_provider.preflight_sms_dependency(http=_Http([]), country="1")

    def test_acquire_falls_back_to_next_provider_without_parallel_purchase(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower,tiger"
        codex_config.SMS_COUNTRY = "10"
        codex_config.SMSBOWER_RANDOM_COUNTRY = False
        codex_config.SMSBOWER_USE_V2 = False
        codex_config.TIGER_SMS_RANDOM_COUNTRY = False
        http = _Http([
            _Response("NO_NUMBERS"),
            _Response(json.dumps({"1": {"dr": {"cost": 0.09, "count": 2}}})),
            _Response(json.dumps({
                "activationId": "chain-tiger-1",
                "phoneNumber": "15550009999",
                "activationCost": 0.02,
                "currency": "USD",
            })),
        ])
        activation_id, phone = sms_provider.acquire_number(http=http, country="1")
        self.assertEqual((activation_id, phone), ("chain-tiger-1", "15550009999"))
        self.assertEqual([call["url"] for call in http.calls], [
            "http://sms.test/handler_api",
            "http://tiger.test/stubs/handler_api.php",
            "http://tiger.test/stubs/handler_api.php",
        ])
        self.assertEqual(sms_provider.get_activation_info(activation_id)["provider"], "tiger")

    def test_tiger_provider_selection_and_number_params_use_common_cny_ceiling(self):
        codex_config.SMS_PROVIDER = "tiger"
        codex_config.SMS_MAX_PRICE = "7.20"
        params = sms_provider._tiger_number_params("dr", "6")
        self.assertEqual(params["action"], "getNumberV2")
        self.assertEqual(params["activationType"], "SMS")
        self.assertEqual(params["maxPrice"], "1.000000")
        self.assertNotIn("minPrice", params)
        self.assertNotIn("phoneException", params)
        self.assertEqual(sms_provider._provider(), "tiger")

    def test_common_cny_ceiling_limits_herosms_and_smspool_requests(self):
        codex_config.SMS_MAX_PRICE = "7.20"
        codex_config.HEROSMS_MAX_PRICE = "1.50"
        codex_config.HEROSMS_COUNTRY = "6"
        hero_params = sms_provider._herosms_number_params("dr", None)
        self.assertEqual(hero_params["maxPrice"], "1.000000")

        codex_config.SMSPOOL_MAX_PRICE = "1.50"
        codex_config.SMSPOOL_COUNTRY = "1"
        codex_config.SMSPOOL_SERVICE = "671"
        pool_params = sms_provider._smspool_number_params("671", None)
        self.assertEqual(pool_params["max_price"], "1.000000")

    def test_provider_specific_usd_caps_can_only_lower_common_cny_ceiling(self):
        codex_config.SMS_MAX_PRICE = "14.40"
        codex_config.HEROSMS_MAX_PRICE = "0.20"
        codex_config.SMSPOOL_MAX_PRICE = "0.30"
        self.assertEqual(
            sms_provider._provider_price_bounds_usd("herosms"),
            (None, Decimal("0.20")),
        )
        self.assertEqual(
            sms_provider._provider_price_bounds_usd("smspool"),
            (None, Decimal("0.30")),
        )

    def test_common_cny_ceiling_hard_rejects_overpriced_herosms_activation(self):
        codex_config.SMS_MAX_PRICE = "7.20"
        codex_config.HEROSMS_MAX_PRICE = ""
        sms_provider._remember_activation("hero-over-cap", "15550001111", {
            "provider": "herosms",
            "price_amount": "1.01",
            "price_currency": "USD",
            "request_max_price_usd": "1.000000",
        })
        with self.assertRaises(sms_provider.SmsNumberRejectedError):
            sms_provider._validate_activation_price("hero-over-cap")

    def test_tiger_acquire_parses_v2_price_and_uses_tiger_endpoint(self):
        codex_config.SMS_PROVIDER = "tiger"
        codex_config.SMS_MAX_PRICE = "1.44"
        http = _Http([
            _Response(json.dumps({"6": {"dr": {"cost": "0.1200", "count": 3}}})),
            _Response(json.dumps({
                "activationId": "tiger-a1",
                "phoneNumber": "15550001111",
                "activationCost": 0.11,
                "currency": 840,
                "countryCode": 6,
                "activationOperator": "any",
            })),
        ])
        activation_id, phone = sms_provider.acquire_number(http=http, country="6")
        self.assertEqual((activation_id, phone), ("tiger-a1", "15550001111"))
        self.assertEqual([call["params"].get("action") for call in http.calls], ["getPrices", "getNumberV2"])
        self.assertTrue(all(call["url"] == "http://tiger.test/stubs/handler_api.php" for call in http.calls))
        self.assertTrue(all(call["params"]["api_key"] == "tiger-key" for call in http.calls))
        self.assertNotIn("minPrice", http.calls[-1]["params"])
        self.assertNotIn("phoneException", http.calls[-1]["params"])
        state = sms_provider.get_activation_info(activation_id)
        self.assertEqual(state["provider"], "tiger")
        self.assertEqual(state["price_amount"], "0.11")
        self.assertEqual(state["price_currency"], "USD")
        self.assertTrue(state["price_validated"])
        self.assertEqual(state["price_limit_configured_currency"], "CNY")

    def test_tiger_status_poll_complete_and_cancel_use_tiger_endpoint(self):
        codex_config.SMS_PROVIDER = "tiger"
        sms_provider._remember_activation("tiger-a2", "15550002222", {
            "provider": "tiger", "service": "dr", "country": "6",
        })
        http = _Http([_Response("STATUS_WAIT_CODE"), _Response("STATUS_OK:987654")])
        with patch.object(sms_provider, "_stop_sleep", side_effect=lambda seconds, quantum=0.25: None):
            self.assertEqual(sms_provider.wait_for_sms_code("tiger-a2", http=http, max_wait=2, poll_interval=0), "987654")
        self.assertTrue(all(call["url"] == "http://tiger.test/stubs/handler_api.php" for call in http.calls))
        sms_provider._remember_activation("tiger-ready", "15550003000", {"provider": "tiger", "country": "6"})
        ready_http = _Http([_Response("ACCESS_READY")])
        self.assertEqual(sms_provider.set_status("tiger-ready", 1, http=ready_http), "ACCESS_READY")
        self.assertEqual(ready_http.calls[0]["params"]["status"], "1")
        sms_provider._remember_activation("tiger-a3", "15550003333", {"provider": "tiger", "country": "6"})
        complete_http = _Http([_Response("ACCESS_ACTIVATION")])
        sms_provider.complete("tiger-a3", http=complete_http)
        self.assertEqual(complete_http.calls[0]["params"]["status"], "6")
        sms_provider._remember_activation("tiger-a4", "15550004444", {"provider": "tiger", "country": "6"})
        cancel_http = _Http([_Response("ACCESS_CANCEL")])
        sms_provider.cancel("tiger-a4", http=cancel_http, background=False)
        self.assertEqual(cancel_http.calls[0]["params"]["status"], "8")

    def test_tiger_access_cancel_status_is_terminal(self):
        codex_config.SMS_PROVIDER = "tiger"
        sms_provider._remember_activation("tiger-cancelled", "15550005555", {"provider": "tiger"})
        http = _Http([_Response("ACCESS_CANCEL")])
        with self.assertRaises(sms_provider.SmsProviderError):
            sms_provider.wait_for_sms_code("tiger-cancelled", http=http, max_wait=2, poll_interval=0)
        self.assertEqual(len(http.calls), 1)

    def test_tiger_over_price_activation_is_released_before_retry(self):
        codex_config.SMS_PROVIDER = "tiger"
        codex_config.SMS_MAX_PRICE = "1.44"
        http = _Http([
            _Response(json.dumps({"6": {"dr": {"cost": "0.1800", "count": 5}}})),
            _Response(json.dumps({"activationId": "tiger-too-high", "phoneNumber": "15550006666", "activationCost": 0.21, "currency": 840})),
            _Response("ACCESS_CANCEL"),
            _Response(json.dumps({"activationId": "tiger-in-range", "phoneNumber": "15550007777", "activationCost": 0.19, "currency": 840})),
        ])
        activation_id, phone = sms_provider.acquire_number(http=http, country="6")
        self.assertEqual((activation_id, phone), ("tiger-in-range", "15550007777"))
        self.assertEqual([call["params"].get("action") for call in http.calls], ["getPrices", "getNumberV2", "setStatus", "getNumberV2"])
        self.assertEqual(http.calls[2]["params"]["status"], "8")
        self.assertEqual(sms_provider.get_activation_info(activation_id)["price_validated"], True)

    def test_tiger_get_number_v2_falls_back_only_for_bad_action(self):
        http = _Http([_Response("BAD_ACTION"), _Response("ACCESS_NUMBER:tiger-v1:15550008888")])
        params, response = sms_provider._request_tiger_number(http, {
            "action": "getNumberV2", "service": "dr", "country": "6",
        })
        self.assertEqual(params["action"], "getNumber")
        self.assertEqual(response, "ACCESS_NUMBER:tiger-v1:15550008888")
        self.assertEqual(len(http.calls), 2)

    def test_tiger_no_numbers_does_not_repeat_v1_purchase(self):
        http = _Http([_Response(json.dumps({"title": "NO_NUMBERS", "details": "No stock"}))])
        with self.assertRaises(sms_provider.SmsNoNumbersError):
            sms_provider._request_tiger_number(http, {"action": "getNumberV2", "service": "dr", "country": "6"})
        self.assertEqual(len(http.calls), 1)

    def test_tiger_errors_map_balance_and_inventory(self):
        codex_config.SMS_PROVIDER = "tiger"
        with self.assertRaises(sms_provider.SmsNoBalanceError):
            sms_provider._request_tiger(_Http([_Response(json.dumps({"title": "NO_BALANCE", "details": "Insufficient balance"}), 402)]), {"action": "getNumberV2"})
        with self.assertRaises(sms_provider.SmsNoNumbersError):
            sms_provider._request_tiger(_Http([_Response(json.dumps({"title": "NO_NUMBERS", "details": "No numbers"}))]), {"action": "getNumberV2"})

    def test_sms_exception_classification_distinguishes_blocked_and_retryable(self):
        blocked = sms_provider.classify_sms_exception(sms_provider.SmsNoBalanceError("NO_BALANCE"))
        config = sms_provider.classify_sms_exception(sms_provider.SmsProviderConfigurationError("bad key"))
        retryable = sms_provider.classify_sms_exception(sms_provider.SmsNoNumbersError("NO_NUMBERS"))

        self.assertEqual((blocked["status"], blocked["error_code"], blocked["retryable"]), ("blocked", "sms_no_balance", False))
        self.assertEqual((config["status"], config["error_code"], config["retryable"]), ("blocked", "sms_provider_configuration", False))
        self.assertEqual((retryable["status"], retryable["error_code"], retryable["retryable"]), ("failed", "sms_no_numbers", True))

    def test_smsbower_v2_request_uses_dynamic_country_and_rmb_cap(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower"
        codex_config.SMS_COUNTRY = ""
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_USE_V2 = True
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        http = _Http([
            _Response(json.dumps({"35": {"dr": {"cost": "0.02", "count": 1}}})),
            _Response(json.dumps({
                "activationId": "a-reference",
                "phoneNumber": "15550003",
                "countryCode": "35",
                "providerId": "p1",
                "activationCost": "0.02",
                "currency": "USD",
            })),
        ])

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda values: None):
            activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("a-reference", "15550003"))
        self.assertEqual([call["params"]["action"] for call in http.calls], ["getPrices", "getNumberV2"])
        self.assertEqual(http.calls[1]["params"]["service"], "dr")
        self.assertEqual(http.calls[1]["params"]["country"], "35")
        self.assertEqual(http.calls[1]["params"]["maxPrice"], "0.034722")

    def test_wrong_service_from_v2_falls_back_with_same_country_and_cap(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower"
        codex_config.SMS_COUNTRY = "10"
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_USE_V2 = True
        http = _Http([
            _Response("WRONG_SERVICE"),
            _Response("ACCESS_NUMBER:legacy-ok:15550005"),
        ])

        activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("legacy-ok", "15550005"))
        self.assertEqual([call["params"]["action"] for call in http.calls], ["getNumberV2", "getNumber"])
        self.assertTrue(all(call["params"]["service"] == "dr" for call in http.calls))
        self.assertTrue(all(call["params"]["country"] == "10" for call in http.calls))
        self.assertTrue(all(call["params"]["maxPrice"] == "0.034722" for call in http.calls))
        self.assertTrue(sms_provider._activation_state(activation_id)["price_validated"])

    def test_legacy_api_uses_reference_country_without_inventory_requests(self):
        codex_config.SMS_COUNTRY = "10"
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_USE_V2 = False
        http = _Http([_Response("ACCESS_NUMBER:a-reference:15550004")])

        activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("a-reference", "15550004"))
        self.assertEqual([call["params"]["country"] for call in http.calls], ["10"])
        self.assertEqual([call["params"]["action"] for call in http.calls], ["getNumber"])
        self.assertEqual(http.calls[0]["params"]["maxPrice"], "0.034722")
        self.assertTrue(sms_provider._activation_state(activation_id)["price_validated"])

    def test_provider_no_numbers_uses_one_concrete_country_request(self):
        codex_config.SMS_PROVIDER_CHAIN = "smsbower"
        codex_config.SMS_COUNTRY = "10"
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_USE_V2 = False
        http = _Http([_Response("NO_NUMBERS")])

        with self.assertRaises(sms_provider.SmsNoNumbersError):
            sms_provider.acquire_number(http=http)

        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0]["params"]["country"], "10")
        self.assertEqual(http.calls[0]["params"]["maxPrice"], "0.034722")

    def test_explicit_country_is_used_when_random_flag_is_set(self):
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMS_COUNTRY = "99"
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        http = _Http([_Response(json.dumps({
            "activationId": "a-fixed",
            "phoneNumber": "84377221015",
            "countryCode": "99",
        }))])

        activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("a-fixed", "84377221015"))
        self.assertEqual(http.calls[0]["params"]["country"], "99")

    def test_get_number_v2_remembers_provider_tier_metadata(self):
        http = _Http([
            _Response(json.dumps({
                "activationId": "a1",
                "phoneNumber": "15550001",
                "providerId": "p9",
                "activationOperator": "op9",
            }))
        ])

        activation_id, phone = sms_provider._acquire_number_once(http, service="dr", country="1")

        self.assertEqual((activation_id, phone), ("a1", "15550001"))
        state = sms_provider._activation_state("a1")
        self.assertEqual(state["provider_id"], "p9")
        self.assertEqual(state["tier"], "op9")
        self.assertEqual(state["acquire_mode"], "getNumberV2")
        self.assertEqual(http.calls[0]["params"]["action"], "getNumberV2")

    def test_smsbower_price_range_is_forwarded(self):
        codex_config.SMSBOWER_MIN_PRICE = "0.10"
        codex_config.SMS_MAX_PRICE = "0.25"

        params = sms_provider._smsbower_number_params("dr", "1")

        self.assertEqual(params["minPrice"], "0.013889")
        self.assertEqual(params["maxPrice"], "0.034722")

    def test_codex_config_loads_smsbower_max_price_from_env(self):
        source = STAGED_CODEX.read_text(encoding="utf-8")

        self.assertIn('SMS_COUNTRY: str = ""', source)
        self.assertIn('SMS_MAX_PRICE: str = ""', source)
        self.assertIn("'SMS_MAX_PRICE': 'str'", source)
        self.assertIn("'SMSBOWER_USD_CNY_RATE': 'str'", source)
        self.assertIn("'SMS_EXCEPT_REGIONS': 'str'", source)

    def test_fx_refresh_log_uses_usd_cny_direction_and_source(self):
        codex_config.SMS_FX_RATE_URL = "https://fx.test/latest?from=CNY&to=USD"
        body = json.dumps({"base": "CNY", "rates": {"USD": 0.125}}).encode("utf-8")
        with patch.object(sms_provider, "urlopen", return_value=BytesIO(body)), self.assertLogs(
            sms_provider.logger, level="INFO"
        ) as captured:
            sms_provider._cny_to_usd_rate()
        output = "\n".join(captured.output)
        self.assertIn("rate_usd_cny=8", output)
        self.assertIn("fx_source=live CNY/USD", output)

    def test_live_cny_usd_rate_is_used(self):
        codex_config.SMS_FX_RATE_URL = "https://fx.test/latest?from=CNY&to=USD"
        body = json.dumps({"base": "CNY", "rates": {"USD": 0.14}}).encode("utf-8")
        with patch.object(sms_provider, "urlopen", return_value=BytesIO(body)) as open_url:
            rate, source = sms_provider._cny_to_usd_rate()
        self.assertEqual(str(rate), "0.14")
        self.assertIn("live CNY/USD", source)
        self.assertIn("fx.test", source)
        self.assertEqual(open_url.call_args.args[0].full_url, codex_config.SMS_FX_RATE_URL)
        # 成功的实时汇率要写回配置，供后续网络失败时回退。
        self.assertEqual(codex_config.SMS_LAST_KNOWN_USD_CNY_RATE, format(Decimal("1") / Decimal("0.14"), "f"))

    def test_smsbower_price_converts_rmb_to_usd_before_request(self):
        codex_config.SMS_MAX_PRICE = "0.25"

        with patch.object(
            sms_provider,
            "_cny_to_usd_rate",
            return_value=(Decimal("0.125"), "live CNY/USD (fixture)"),
        ):
            params = sms_provider._smsbower_number_params("dr", "35")

        self.assertEqual(params["country"], "35")
        self.assertEqual(params["maxPrice"], "0.031250")

    def test_failure_falls_back_to_last_known_rate_instead_of_aborting(self):
        """实时汇率源全部失败时回退到最近已知汇率，不再中断取号。"""
        codex_config.SMS_FX_RATE_URLS = "https://fx1.test/latest,https://fx2.test/latest"
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = "6.70"
        with patch.object(sms_provider, "urlopen", side_effect=OSError("offline")):
            rate, source = sms_provider._cny_to_usd_rate()
        self.assertIn("last known", source)
        self.assertAlmostEqual(float(rate), 1 / 6.70, places=6)

    def test_fallback_prefers_remembered_over_configured(self):
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = "6.70"
        codex_config.SMSBOWER_USD_CNY_RATE = "7.2"
        rate, source = sms_provider._fallback_usd_cny_rate()
        self.assertIn("last known", source)
        self.assertAlmostEqual(float(rate), 1 / 6.70, places=6)

    def test_fallback_does_not_use_fixed_legacy_rate(self):
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = ""
        codex_config.SMSBOWER_USD_CNY_RATE = "7.2"
        with self.assertRaises(sms_provider.SmsProviderConfigurationError):
            sms_provider._fallback_usd_cny_rate()

    def test_successful_rate_is_persisted_for_restart_fallback(self):
        original_cache = getattr(codex_config, "SMS_FX_CACHE_FILE", "")
        original_known = codex_config.SMS_LAST_KNOWN_USD_CNY_RATE
        with tempfile.TemporaryDirectory() as directory:
            codex_config.SMS_FX_CACHE_FILE = str(Path(directory) / "fx.json")
            codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = ""
            sms_provider._remember_usd_cny_rate(Decimal("6.8"))
            codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = ""
            rate, source = sms_provider._fallback_usd_cny_rate()
        codex_config.SMS_FX_CACHE_FILE = original_cache
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = original_known
        self.assertAlmostEqual(float(rate), 1 / 6.8, places=6)
        self.assertIn("persisted last known", source)

    def test_price_bounds_convert_rmb_values_and_preserve_order(self):
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_MIN_PRICE = "0.30"

        with patch.object(
            sms_provider,
            "_cny_to_usd_rate",
            return_value=(Decimal("0.1"), "configured USD/CNY (fixture)"),
        ):
            minimum, maximum = sms_provider._smsbower_price_bounds_usd()
            params = sms_provider._smsbower_number_params("dr", "35")

        self.assertEqual(minimum, Decimal("0.030"))
        self.assertEqual(maximum, Decimal("0.025"))
        self.assertGreater(minimum, maximum)
        self.assertEqual(params["maxPrice"], "0.025000")

    def test_wait_filters_history_and_retries_once_with_zero_interval(self):
        sms_provider._remember_activation(
            "a1",
            "15550001",
            {"provider": "smsbower", "service": "dr", "country": "1", "provider_id": "p9", "tier": "op9"},
        )
        sms_provider._remember_code("a1", "111")
        http = _Http([
            _Response("STATUS_OK:111"),
            _Response("STATUS_WAIT_RETRY:111"),
            _Response("OK"),
            _Response("STATUS_WAIT_RETRY:111"),
            _Response("STATUS_OK:222"),
        ])
        sleeps = []

        with patch.object(
            sms_provider,
            "_stop_sleep",
            side_effect=lambda seconds, quantum=0.25: sleeps.append(seconds),
        ):
            code = sms_provider.wait_for_sms_code("a1", http=http, max_wait=2, poll_interval=0)

        self.assertEqual(code, "222")
        status_calls = [
            call for call in http.calls
            if call["params"].get("action") == "setStatus"
        ]
        self.assertEqual(len(status_calls), 1)
        self.assertEqual(status_calls[0]["params"]["status"], "3")
        self.assertTrue(sleeps)
        self.assertTrue(all(seconds == 0 for seconds in sleeps))
        self.assertIn("222", sms_provider._CODE_HISTORY["a1"])

    def test_wait_uses_monotonic_deadline_and_zero_wait_times_out(self):
        source = inspect.getsource(sms_provider.wait_for_sms_code)
        self.assertIn("time.monotonic()", source)
        self.assertNotIn("time.time()", source)

        http = _Http([])
        with self.assertRaises(sms_provider.SmsCodeTimeout):
            sms_provider.wait_for_sms_code("missing", http=http, max_wait=0, poll_interval=0)
        self.assertEqual(http.calls, [])

    def test_openai_phone_rejection_codes_are_number_quality_failures(self):
        self.assertEqual(
            sms_provider._classify_failure(
                "add_phone_response=detail={\"code\":\"fraud_guard\"}"
            ),
            "number_rejected",
        )
        self.assertEqual(
            sms_provider._classify_failure(
                "add_phone_response=detail={\"code\":\"voip_phone_disallowed\"}"
            ),
            "number_rejected",
        )

    def test_voip_rejection_blocks_only_the_specific_number(self):
        sms_provider._remember_activation(
            "voip-1",
            "15550008",
            {"provider": "smsbower", "service": "dr", "country": "12", "tier": "default"},
        )
        category = sms_provider.report_failure(
            "voip-1",
            'add_phone_response={"code":"voip_phone_disallowed"}',
        )
        state = sms_provider._activation_state("voip-1")
        self.assertEqual(category, "number_rejected")
        self.assertFalse(sms_provider._country_is_recently_rejected(state))
        self.assertTrue(sms_provider._quality_voip_number_is_blocked("15550008"))
        self.assertTrue(sms_provider._quality_number_is_blocked("15550008"))
        self.assertFalse(sms_provider._country_is_recently_rejected({
            "provider": "smsbower", "service": "dr", "country": "36",
        }))
        self.assertEqual(sms_provider.get_sms_runtime_metrics()["rejected_countries"], 0)

    def test_voip_rejection_does_not_block_same_country_candidates(self):
        sms_provider._remember_activation(
            "voip-same-country",
            "15550009",
            {"provider": "smsbower", "service": "dr", "country": "12", "tier": "default"},
        )
        sms_provider.report_failure(
            "voip-same-country",
            'add_phone_response={"code":"voip_phone_disallowed"}',
        )
        self.assertTrue(sms_provider._quality_voip_number_is_blocked("15550009"))
        self.assertFalse(sms_provider._voip_country_is_blocked({
            "provider": "smsbower", "service": "dr", "country": "12",
        }))
        self.assertFalse(sms_provider._number_is_recently_rejected("15550010"))

    def test_fraud_guard_does_not_cool_entire_country(self):
        sms_provider._remember_activation(
            "fraud-1",
            "15550010",
            {"provider": "smsbower", "service": "dr", "country": "12", "tier": "default"},
        )
        sms_provider.report_failure(
            "fraud-1",
            'add_phone_response={"code":"fraud_guard"}',
        )
        self.assertEqual(sms_provider.get_sms_runtime_metrics()["rejected_countries"], 0)

    def test_sms_quality_ledger_persists_aggregate_scores_without_plain_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "sms-quality.json"
            codex_config.SMS_QUALITY_FILE = str(ledger_path)
            for index in range(3):
                activation_id = f"quality-good-{index}"
                sms_provider._remember_activation(activation_id, f"1555001000{index}", {
                    "provider": "smsbower", "service": "dr", "country": "12", "tier": "default",
                })
                sms_provider.report_success(activation_id)
            for index in range(3):
                activation_id = f"quality-bad-{index}"
                sms_provider._remember_activation(activation_id, f"1555002000{index}", {
                    "provider": "smsbower", "service": "dr", "country": "73", "tier": "default",
                })
                sms_provider.report_failure(activation_id, 'add_phone_response={"code":"fraud_guard"}')

            ranked = sms_provider._quality_rank_candidates([
                {"country": "73"}, {"country": "12"}, {"country": "99"},
            ], "smsbower", "dr")
            self.assertEqual(ranked[0]["country"], "12")
            payload = ledger_path.read_text(encoding="utf-8")
            self.assertNotIn("15550010000", payload)
            self.assertNotIn("15550020000", payload)
            self.assertIn('"successes":3', payload)

            sms_provider._reset_runtime_state_for_tests()
            summary = sms_provider.get_sms_runtime_metrics()["quality"]["buckets"]
            self.assertTrue(any(row["country"] == "12" and row["success_rate"] == 1.0 for row in summary))

    def test_smsbower_default_tier_never_cools_entire_provider(self):
        codex_config.SMS_TIER_FAILURE_THRESHOLD = 1
        sms_provider._remember_activation(
            "default-1",
            "15550009",
            {"provider": "smsbower", "service": "dr", "country": "1", "tier": "default"},
        )

        sms_provider.report_failure("default-1", "send_not_accepted")
        state = sms_provider._activation_state("default-1")

        self.assertFalse(sms_provider._tier_is_cooled(state))
        self.assertEqual(sms_provider.get_sms_runtime_metrics()["cooled_tiers"], 0)
        self.assertEqual(sms_provider._cooldown_provider_ids("dr", "1"), set())

    def test_tier_failure_cooldown_enters_smsbower_except_provider_ids(self):
        codex_config.SMS_TIER_FAILURE_THRESHOLD = 1
        codex_config.SMS_TIER_COOLDOWN_SECONDS = 600
        codex_config.SMSBOWER_EXCEPT_PROVIDER_IDS = "p1"
        codex_config.SMSBOWER_RANDOM_COUNTRY = False
        sms_provider._remember_activation(
            "a1",
            "15550001",
            {"provider": "smsbower", "service": "dr", "country": "1", "provider_id": "p9", "tier": "op9"},
        )

        category = sms_provider.report_failure("a1", sms_provider.SmsCodeTimeout("timeout"))
        params = sms_provider._smsbower_number_params("dr", "1")

        self.assertEqual(category, "code_timeout")
        self.assertTrue(sms_provider._tier_is_cooled(sms_provider._activation_state("a1")))
        self.assertEqual(set(params["exceptProviderIds"].split(",")), {"p1", "p9"})

    def test_cancel_and_report_failure_releases_before_feedback_with_snapshot(self):
        codex_config.SMS_TIER_FAILURE_THRESHOLD = 1
        sms_provider._remember_activation(
            "b-failed",
            "15550007",
            {"provider": "smsbower", "service": "dr", "country": "1", "provider_id": "p9", "tier": "op9"},
        )
        events = []
        http = _Http([_Response("OK")])
        original_get = http.get

        def record_release(url, params=None, **kwargs):
            if (params or {}).get("action") == "setStatus":
                events.append(("release", (params or {}).get("status")))
            return original_get(url, params=params, **kwargs)

        http.get = record_release
        original_record = sms_provider._record_tier_failure

        def record_failure(state, category):
            events.append(("failure", category))
            return original_record(state, category)

        with patch.object(sms_provider, "_record_tier_failure", side_effect=record_failure):
            category = sms_provider.cancel_and_report_failure("b-failed", http, "send_not_accepted")

        self.assertEqual(category, "send_failed")
        self.assertEqual(events, [("release", "8"), ("failure", "send_failed")])
        self.assertEqual(sms_provider._activation_state("b-failed"), {})
        self.assertEqual(sms_provider._cooldown_provider_ids("dr", "1"), {"p9"})

    def test_actual_price_above_converted_max_is_released_and_next_candidate_is_used(self):
        codex_config.SMS_MAX_PRICE = "0.4"
        codex_config.SMS_LAST_KNOWN_USD_CNY_RATE = "10"
        codex_config.SMSBOWER_RANDOM_COUNTRY_ATTEMPTS = 2
        http = _Http([
            _Response(json.dumps({
                "activationId": "too-expensive",
                "phoneNumber": "351910000001",
                "countryCode": "10",
                "price": "0.05",
                "currency": "USD",
            })),
            _Response("OK"),
            _Response(json.dumps({
                "activationId": "within-budget",
                "phoneNumber": "351910000002",
                "countryCode": "35",
                "price": "0.03",
                "currency": "USD",
            })),
        ])

        activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("within-budget", "351910000002"))
        self.assertEqual(
            [call["params"].get("status") for call in http.calls if call["params"].get("action") == "setStatus"],
            ["8"],
        )
        state = sms_provider.get_activation_info(activation_id)
        self.assertEqual(state["price_amount"], "0.03")
        self.assertEqual(state["price_currency"], "USD")
        self.assertTrue(state["price_validated"])
        self.assertEqual(state["price_limit_max"], "0.04")
        self.assertEqual(state["price_limit_configured_max"], "0.4")
        self.assertEqual(state["price_limit_configured_currency"], "CNY")


    def test_activation_price_uses_explicit_currency_and_quote_fallback(self):
        self.assertEqual(sms_provider._activation_price_metadata(
            {"price": "0.031", "currency": "USD"}, "smsbower", {"cost": "0.04"}
        ), {"price_amount": "0.031", "price_currency": "USD", "price_source": "SMSBOWER 激活响应"})
        self.assertEqual(sms_provider._activation_price_metadata({"cost": "0.04"}, "smsbower"), {
            "price_amount": "0.04", "price_currency": "USD", "price_source": "SMSBOWER 激活响应",
        })
        self.assertEqual(sms_provider._activation_price_metadata(None, "smsbower", {"cost": "0.04"}), {
            "price_amount": "0.04", "price_currency": "USD", "price_source": "SMSBower getPrices 报价"
        })

    def test_complete_returns_phone_and_price_snapshot_before_forgetting_state(self):
        sms_provider._remember_activation("b1", "15550002", {
            "provider": "smsbower", "country": "1", "price_amount": "0.02", "price_currency": "USD",
        })
        snapshot = sms_provider.complete("b1", http=_Http([_Response("OK")]))
        self.assertEqual(snapshot, {
            "phone_number": "+15550002", "country": "1", "provider": "smsbower",
            "price_amount": "0.02", "price_currency": "USD",
        })
        self.assertEqual(sms_provider._activation_state("b1"), {})

    def test_complete_and_cancel_forget_activation_state(self):
        sms_provider._remember_activation("b1", "15550003", {"provider": "smsbower"})
        sms_provider.complete("b1", http=_Http([_Response("OK")]))
        self.assertEqual(sms_provider._activation_state("b1"), {})
        self.assertNotIn("b1", sms_provider._CODE_HISTORY)

        sms_provider._remember_activation("b2", "15550004", {"provider": "smsbower"})
        http = _Http([_Response("OK")])
        result = sms_provider.cancel("b2", http=http, background=False)
        self.assertEqual(result["release"], "cancelled")
        self.assertEqual(result["status"], 8)
        self.assertEqual(http.calls[0]["params"]["status"], "8")
        self.assertEqual(sms_provider._activation_state("b2"), {})

    def test_cancel_after_code_uses_completion_status_not_cancel_status(self):
        sms_provider._remember_activation("b3", "15550005", {"provider": "smsbower"})
        sms_provider._remember_code("b3", "123456")
        http = _Http([_Response("OK")])
        result = sms_provider.cancel("b3", http=http, background=False)
        self.assertEqual(result["release"], "closed_after_code")
        self.assertEqual(result["status"], 6)
        self.assertEqual(http.calls[0]["params"]["status"], "6")
        self.assertEqual(sms_provider._activation_state("b3"), {})

    def test_cancel_does_not_retry_terminal_release_failure(self):
        codex_config.SMS_RELEASE_RETRIES = 3
        codex_config.SMS_RELEASE_RETRY_DELAY = 0
        sms_provider._remember_activation("release-terminal", "15550004444", {"provider": "smsbower"})
        http = _Http([_Response("BAD_STATUS")])
        result = sms_provider.cancel("release-terminal", http=http, background=False)
        self.assertEqual(result["release"], "failed")
        self.assertEqual(len(http.calls), 1)

    def test_cancel_retries_transient_release_failure(self):
        codex_config.SMS_RELEASE_RETRIES = 2
        codex_config.SMS_RELEASE_RETRY_DELAY = 0
        sms_provider._remember_activation("release-retry", "15550004444", {"provider": "smsbower"})
        http = _Http([_Response("temporary", status_code=503), _Response("ACCESS_CANCEL")])
        result = sms_provider.cancel("release-retry", http=http, background=False)
        self.assertEqual(result["release"], "cancelled")
        self.assertEqual(len(http.calls), 2)
        self.assertEqual([call["params"]["status"] for call in http.calls], ["8", "8"])

    def test_roxy_feedback_hooks_release_before_failure_feedback(self):
        source = ROXY_SOURCE.read_text(encoding="utf-8")
        ast.parse(source)
        success = source.index("sms_provider.report_success(activation_id)")
        complete = source.index("sms_provider.complete(activation_id, http)")
        config_branch = source.index("sms_provider.SmsProviderConfigurationError")
        config_release_feedback = source.index("sms_provider.cancel_and_report_failure(activation_id, http, exc)", config_branch)
        generic_branch = source.index("except Exception as exc", config_release_feedback)
        generic_release_feedback = source.index(
            "sms_provider.cancel_and_report_failure(", generic_branch
        )

        self.assertLess(success, complete)
        self.assertLess(config_release_feedback, generic_branch)
        self.assertGreater(generic_release_feedback, generic_branch)
        self.assertIn("raise", source[config_release_feedback:generic_branch])


    def test_browser_use_feedback_hooks_release_before_failure_feedback(self):
        source = BROWSER_USE_SOURCE.read_text(encoding="utf-8")
        ast.parse(source)
        config_branch = source.index("sms_provider.SmsNoBalanceError")
        config_release_feedback = source.index(
            "sms_provider.cancel_and_report_failure(activation_id, http, exc)", config_branch
        )
        generic_branch = source.index("except Exception as exc", config_release_feedback)
        generic_release_feedback = source.index(
            "sms_provider.cancel_and_report_failure(", generic_branch
        )

        self.assertLess(config_branch, config_release_feedback)
        self.assertLess(config_release_feedback, generic_branch)
        self.assertGreater(generic_release_feedback, generic_branch)

    def test_herosms_compatible_protocol_acquire_poll_complete_and_cancel(self):
        codex_config.SMS_PROVIDER_CHAIN = "herosms"
        codex_config.HEROSMS_API_KEY = "hero-key"
        codex_config.HEROSMS_COUNTRY = "6"
        codex_config.HEROSMS_USE_V2 = False
        http = _Http([_Response("ACCESS_NUMBER:hero-a:15550001111")])
        activation_id, phone = sms_provider.acquire_number(http=http)
        self.assertEqual((activation_id, phone), ("hero-a", "15550001111"))
        self.assertEqual(http.calls[0]["url"], "http://hero.test/stubs/handler_api.php")
        self.assertEqual(http.calls[0]["params"]["action"], "getNumber")

        status_http = _Http([_Response("STATUS_WAIT_CODE"), _Response("STATUS_OK:654321")])
        self.assertEqual(
            sms_provider.wait_for_sms_code("hero-a", http=status_http, max_wait=2, poll_interval=0),
            "654321",
        )
        self.assertEqual([call["params"]["action"] for call in status_http.calls], ["getStatus", "getStatus"])

        complete_http = _Http([_Response("ACCESS_ACTIVATION")])
        sms_provider.complete("hero-a", http=complete_http)
        self.assertEqual(complete_http.calls[0]["params"]["status"], "6")

        sms_provider._remember_activation("hero-b", "15550002222", {"provider": "herosms"})
        cancel_http = _Http([_Response("ACCESS_CANCEL")])
        result = sms_provider.cancel("hero-b", http=cancel_http, background=False)
        self.assertEqual(result["status"], 8)
        self.assertEqual(cancel_http.calls[0]["params"]["status"], "8")

    def test_herosms_v2_uses_get_status_v2_and_extracts_json_code(self):
        codex_config.SMS_PROVIDER_CHAIN = "herosms"
        codex_config.HEROSMS_API_KEY = "hero-key"
        codex_config.HEROSMS_COUNTRY = "6"
        codex_config.HEROSMS_USE_V2 = True
        purchase_http = _Http([_Response(json.dumps({
            "activationId": "hero-v2",
            "phoneNumber": "15550009999",
            "countryCode": "6",
        }))])
        activation_id, phone = sms_provider.acquire_number(http=purchase_http)
        self.assertEqual((activation_id, phone), ("hero-v2", "15550009999"))
        self.assertEqual(sms_provider.get_activation_info(activation_id)["status_action"], "getStatusV2")

        status_http = _Http([
            _Response(json.dumps({"status": "pending"})),
            _Response(json.dumps({"sms": {"code": "246810"}})),
        ])
        self.assertEqual(
            sms_provider.wait_for_sms_code(activation_id, http=status_http, max_wait=2, poll_interval=0),
            "246810",
        )
        self.assertEqual([call["params"]["action"] for call in status_http.calls], ["getStatusV2", "getStatusV2"])

    def test_herosms_maps_no_numbers_and_bad_key_without_network_fallback(self):
        codex_config.HEROSMS_API_KEY = "hero-key"
        with self.assertRaises(sms_provider.SmsNoNumbersError):
            sms_provider._request_herosms(_Http([_Response("NO_NUMBERS")]), {"action": "getNumber"})
        with self.assertRaises(sms_provider.SmsProviderConfigurationError):
            sms_provider._request_herosms(_Http([_Response("BAD_KEY")]), {"action": "getNumber"})

    def test_smspool_native_protocol_acquire_poll_cancel_and_skip_complete(self):
        codex_config.SMS_PROVIDER_CHAIN = "smspool"
        codex_config.SMSPOOL_API_KEY = "pool-key"
        codex_config.SMSPOOL_COUNTRY = "1"
        codex_config.SMSPOOL_SERVICE = "671"
        codex_config.SMSPOOL_MAX_PRICE = "0.20"
        purchase_http = _Http([_Response(json.dumps({
            "success": 1, "order_id": "pool-a", "number": "15550003333", "cost": "0.11",
        }))])
        activation_id, phone = sms_provider.acquire_number(http=purchase_http)
        self.assertEqual((activation_id, phone), ("pool-a", "15550003333"))
        self.assertEqual(purchase_http.calls[0]["url"], "http://pool.test/purchase/sms")
        self.assertEqual(purchase_http.calls[0]["data"]["country"], "1")
        self.assertEqual(purchase_http.calls[0]["data"]["service"], "671")
        self.assertEqual(purchase_http.calls[0]["data"]["max_price"], "0.20")
        self.assertEqual(purchase_http.calls[0]["data"]["pricing_option"], "0")

        status_http = _Http([_Response(json.dumps({"success": 1, "status": "pending"})), _Response(json.dumps({
            "success": 1, "status": "completed", "sms": "Your verification code is 765432",
        }))])
        self.assertEqual(
            sms_provider.wait_for_sms_code("pool-a", http=status_http, max_wait=2, poll_interval=0),
            "765432",
        )
        complete_http = _Http([])
        sms_provider.complete("pool-a", http=complete_http)
        self.assertEqual(complete_http.calls, [])

        sms_provider._remember_activation("pool-b", "15550004444", {"provider": "smspool"})
        cancel_http = _Http([_Response(json.dumps({"success": 1, "message": "cancelled"}))])
        result = sms_provider.cancel("pool-b", http=cancel_http, background=False)
        self.assertEqual(result["status"], 8)
        self.assertEqual(cancel_http.calls[0]["url"], "http://pool.test/sms/cancel")

    def test_smspool_terminal_order_does_not_wait_until_timeout(self):
        codex_config.SMSPOOL_API_KEY = "pool-key"
        sms_provider._remember_activation("pool-terminal", "15550005555", {"provider": "smspool"})
        http = _Http([_Response(json.dumps({"success": 1, "status": "completed"}))])
        with self.assertRaises(sms_provider.SmsProviderError):
            sms_provider.wait_for_sms_code("pool-terminal", http=http, max_wait=10, poll_interval=0)
        self.assertEqual(len(http.calls), 1)

    def test_unconfigured_herosms_is_automatically_skipped_from_chain(self):
        codex_config.SMS_PROVIDER_CHAIN = "herosms,smsbower"
        codex_config.HEROSMS_API_KEY = ""
        codex_config.SMSBOWER_API_KEY = "test-key"
        self.assertEqual(sms_provider._attempt_provider_chain(), ["smsbower"])

    def test_preflight_uses_provider_specific_service_and_country(self):
        codex_config.SMS_PROVIDER_CHAIN = "smspool"
        codex_config.SMSPOOL_API_KEY = "pool-key"
        codex_config.SMSPOOL_SERVICE = "671"
        codex_config.SMSPOOL_COUNTRY = "1"
        codex_config.SMS_SERVICE = "dr"
        codex_config.SMS_COUNTRY = "10"
        result = sms_provider.preflight_sms_dependency(http=_Http([]))
        self.assertEqual(result["provider"], "smspool")
        self.assertEqual(result["service"], "671")
        self.assertEqual(result["country"], "1")
        self.assertTrue(result["deferred"])


if __name__ == "__main__":
    unittest.main()
