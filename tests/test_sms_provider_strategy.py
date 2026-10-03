# -*- coding: utf-8 -*-
import ast
from io import BytesIO
import importlib.util
import inspect
import json
import sys
import types
import unittest
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
            SMS_COUNTRY="1",
            SMS_REQUEST_TIMEOUT=5,
            SMS_FX_RATE_URL="",
            SMS_FX_RATE_TTL=900,
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
            SMSBOWER_USD_CNY_RATE="7.2",
            SMSBOWER_PROVIDER_IDS="",
            SMSBOWER_EXCEPT_PROVIDER_IDS="",
            SMSBOWER_PHONE_EXCEPTION="",
            SMSBOWER_RANDOM_COUNTRY=True,
            SMSBOWER_RANDOM_COUNTRY_ATTEMPTS=12,
            SMS_NUMBER_ACQUIRE_RETRIES=3,
            SMS_NUMBER_REJECT_TTL=1800,
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
    def setUp(self):
        sms_provider._reset_runtime_state_for_tests()
        codex_config.SMS_PROVIDER = "smsbower"
        codex_config.SMS_SERVICE = "dr"
        codex_config.SMS_COUNTRY = "1"
        codex_config.SMS_FX_RATE_URL = ""
        codex_config.SMSBOWER_USE_V2 = True
        codex_config.TIGER_SMS_USE_V2 = True
        codex_config.TIGER_SMS_PROVIDER_IDS = ""
        codex_config.TIGER_SMS_EXCEPT_PROVIDER_IDS = ""
        codex_config.TIGER_SMS_RANDOM_COUNTRY = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMS_MAX_PRICE = ""
        codex_config.SMSBOWER_MIN_PRICE = ""
        codex_config.SMSBOWER_USD_CNY_RATE = "7.2"
        codex_config.TIGER_SMS_API_KEY = "tiger-key"
        codex_config.TIGER_SMS_API_BASE = "http://tiger.test/stubs/handler_api.php"
        codex_config.TIGER_SMS_USE_V2 = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY = True
        codex_config.TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMSBOWER_RANDOM_COUNTRY = True
        codex_config.SMSBOWER_RANDOM_COUNTRY_ATTEMPTS = 12
        codex_config.SMSBOWER_EXCEPT_PROVIDER_IDS = ""
        codex_config.SMS_TIER_FAILURE_THRESHOLD = 2
        codex_config.SMS_TIER_COOLDOWN_SECONDS = 2700

    def test_random_country_candidates_filter_price_stock_and_shuffle(self):
        codex_config.SMSBOWER_MIN_PRICE = "0.10"
        codex_config.SMS_MAX_PRICE = "0.25"
        http = _Http([_Response(json.dumps({
            "10": {"dr": {"cost": 0.31, "count": 10}},
            "12": {"dr": {"cost": 0.05, "count": 0}},
            "16": {"dr": {"cost": 0.054, "count": 99}},
            "36": {"dr": {"cost": 0.031, "count": 50}},
            "44": {"dr": {"cost": 0.010, "count": 25}},
        }))])

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda rows: rows.reverse()):
            candidates = sms_provider._smsbower_random_country_candidates(http, "dr")

        self.assertEqual([row["country"] for row in candidates], ["36"])
        self.assertEqual(http.calls[0]["params"]["action"], "getPrices")
        self.assertEqual(http.calls[0]["params"]["service"], "dr")

    def test_preflight_smsbower_uses_get_prices_without_consuming_number(self):
        codex_config.SMSBOWER_RANDOM_COUNTRY = False
        http = _Http([_Response(json.dumps({
            "1": {"dr": {"cost": 0.08, "count": 4}},
        }))])

        result = sms_provider.preflight_sms_dependency(http=http)

        self.assertEqual(result["probe"], "getPrices")
        self.assertTrue(result["available"])
        self.assertEqual([call["params"].get("action") for call in http.calls], ["getPrices"])
        self.assertFalse(http.closed)

    def test_preflight_smsbower_no_inventory_does_not_call_get_number(self):
        codex_config.SMSBOWER_RANDOM_COUNTRY = False
        http = _Http([_Response(json.dumps({
            "1": {"dr": {"cost": 0.08, "count": 0}},
        }))])

        with self.assertRaises(sms_provider.SmsNoNumbersError):
            sms_provider.preflight_sms_dependency(http=http)

        self.assertEqual([call["params"].get("action") for call in http.calls], ["getPrices"])
        self.assertNotIn("getNumber", [call["params"].get("action") for call in http.calls])


    def test_unsupported_legacy_provider_falls_back_to_smsbower(self):
        codex_config.SMS_PROVIDER = "h"
        self.assertEqual(sms_provider._provider(), "smsbower")

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

    def test_acquire_number_uses_random_country_and_falls_back_on_no_numbers(self):
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMS_NUMBER_ACQUIRE_RETRIES = 3
        http = _Http([
            _Response(json.dumps({
                "16": {"dr": {"cost": 0.054, "count": 99}},
                "36": {"dr": {"cost": 0.031, "count": 50}},
                "35": {"dr": {"cost": 0.033, "count": 30}},
            })),
            _Response("NO_NUMBERS"),
            _Response("NO_NUMBERS"),
            _Response(json.dumps({
                "activationId": "a-random",
                "phoneNumber": "15550003",
                "providerId": "p1",
            })),
        ])

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda rows: None):
            activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("a-random", "15550003"))
        number_calls = [call for call in http.calls if call["params"].get("action") != "getPrices"]
        self.assertEqual([call["params"]["country"] for call in number_calls], ["36", "36", "35"])
        self.assertTrue(all(call["params"]["maxPrice"] == "0.034722" for call in number_calls))
        state = sms_provider._activation_state(activation_id)
        self.assertEqual(state["country"], "35")

    def test_explicit_country_bypasses_random_country_pool(self):
        codex_config.SMS_MAX_PRICE = "0.25"
        http = _Http([
            _Response(json.dumps({"10": {"dr": {"cost": 0.03, "count": 12}}})),
            _Response(json.dumps({
                "activationId": "a-fixed",
                "phoneNumber": "84377221015",
            })),
        ])

        activation_id, phone = sms_provider.acquire_number(http=http, country="10")

        self.assertEqual((activation_id, phone), ("a-fixed", "84377221015"))
        number_calls = [call for call in http.calls if call["params"].get("action") != "getPrices"]
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(len(number_calls), 1)
        self.assertEqual(number_calls[0]["params"]["country"], "10")
        self.assertEqual(number_calls[0]["params"]["maxPrice"], "0.034722")

    def test_random_country_without_max_price_uses_configured_country(self):
        codex_config.SMS_MAX_PRICE = ""
        http = _Http([
            _Response(json.dumps({"1": {"dr": {"cost": 0.03, "count": 12}}})),
            _Response(json.dumps({
                "activationId": "a-configured",
                "phoneNumber": "84377221015",
            })),
        ])

        activation_id, phone = sms_provider.acquire_number(http=http)

        self.assertEqual((activation_id, phone), ("a-configured", "84377221015"))
        number_calls = [call for call in http.calls if call["params"].get("action") != "getPrices"]
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(len(number_calls), 1)
        self.assertEqual(number_calls[0]["params"]["country"], "1")

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

        self.assertIn('SMS_MAX_PRICE: str = ""', source)
        self.assertIn("'SMS_MAX_PRICE': 'str'", source)
        self.assertIn("'SMSBOWER_USD_CNY_RATE': 'str'", source)

    def test_live_cny_usd_rate_is_used_and_failure_stops_price_limited_purchase(self):
        codex_config.SMS_FX_RATE_URL = "https://fx.test/latest?from=CNY&to=USD"
        body = json.dumps({"base": "CNY", "rates": {"USD": 0.14}}).encode("utf-8")
        with patch.object(sms_provider, "urlopen", return_value=BytesIO(body)) as open_url:
            rate, source = sms_provider._cny_to_usd_rate()
        self.assertEqual(str(rate), "0.14")
        self.assertEqual(source, "live CNY/USD")
        self.assertEqual(open_url.call_args.args[0].full_url, codex_config.SMS_FX_RATE_URL)

        sms_provider._reset_runtime_state_for_tests()
        with patch.object(sms_provider, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(sms_provider.SmsProviderConfigurationError):
                sms_provider._cny_to_usd_rate()

    def test_price_bounds_validate_fx_rate_and_order(self):
        codex_config.SMS_FX_RATE_URL = ""
        codex_config.SMS_MAX_PRICE = "0.25"
        for rate in ("0", "NaN", "Infinity", "bad"):
            with self.subTest(rate=rate):
                codex_config.SMSBOWER_USD_CNY_RATE = rate
                with self.assertRaises(sms_provider.SmsProviderConfigurationError):
                    sms_provider._smsbower_price_bounds_usd()

        codex_config.SMSBOWER_USD_CNY_RATE = "7.2"
        codex_config.SMSBOWER_MIN_PRICE = "0.30"
        with self.assertRaises(sms_provider.SmsProviderConfigurationError):
            sms_provider._smsbower_price_bounds_usd()

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

    def test_actual_price_above_max_is_released_and_next_candidate_is_used(self):
        codex_config.SMS_MAX_PRICE = "0.25"
        codex_config.SMSBOWER_RANDOM_COUNTRY_ATTEMPTS = 2
        http = _Http([
            _Response(json.dumps({
                "36": {"dr": {"cost": 0.03, "count": 10}},
                "35": {"dr": {"cost": 0.03, "count": 10}},
            })),
            _Response(json.dumps({
                "activationId": "too-expensive",
                "phoneNumber": "351910000001",
                "price": "0.05",
                "currency": "USD",
            })),
            _Response("OK"),
            _Response(json.dumps({
                "activationId": "within-budget",
                "phoneNumber": "351910000002",
                "price": "0.03",
                "currency": "USD",
            })),
        ])

        with patch.object(sms_provider.random, "shuffle", side_effect=lambda rows: None):
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
        self.assertLess(float(state["price_limit_max"]), 0.035)


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
        sms_provider.cancel("b2", http=http, background=False)
        self.assertEqual(sms_provider._activation_state("b2"), {})

    def test_roxy_feedback_hooks_release_before_failure_feedback(self):
        source = ROXY_SOURCE.read_text(encoding="utf-8")
        ast.parse(source)
        success = source.index("sms_provider.report_success(activation_id)")
        complete = source.index("sms_provider.complete(activation_id, http)")
        config_branch = source.index("sms_provider.SmsProviderConfigurationError")
        config_release_feedback = source.index("sms_provider.cancel_and_report_failure(activation_id, http, exc)", config_branch)
        generic_branch = source.index("except Exception as exc", config_release_feedback)
        generic_release_feedback = source.index("sms_provider.cancel_and_report_failure(activation_id, http, exc)", generic_branch)

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
            "sms_provider.cancel_and_report_failure(activation_id, http, exc)", generic_branch
        )

        self.assertLess(config_branch, config_release_feedback)
        self.assertLess(config_release_feedback, generic_branch)
        self.assertGreater(generic_release_feedback, generic_branch)


if __name__ == "__main__":
    unittest.main()
