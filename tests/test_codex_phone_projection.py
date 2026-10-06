import json
import unittest
from pathlib import Path

from core import db


class CodexPhoneProjectionTests(unittest.TestCase):
    def test_codex_record_falls_back_to_account_phone_snapshot(self):
        account_payload = {
            "extra_json": json.dumps({
                "codex": {
                    "phone_activation": {
                        "phone_number": "+15550001111",
                        "phone_country_name": "United States",
                        "price_amount": "0.02",
                        "price_currency": "USD",
                        "price_limit_fx_rate": "0.148932",
                    }
                }
            })
        }
        content = {
            "_filename": "codex-phone@example.test-free.json",
            "email": "phone@example.test",
            "type": "codex",
        }

        record = db._codex_content_to_record(content, account_payload)

        self.assertEqual(record["phone"], "+15550001111")
        self.assertEqual(record["phone_country"], "United States")
        self.assertEqual(record["phone_price"], "0.02 USD")
        self.assertEqual(record["phone_price_cny"], "0.134289")
        self.assertEqual(record["phone_activation"]["phone_number"], "+15550001111")

    def test_credential_snapshot_overrides_account_snapshot_without_losing_other_fields(self):
        account_payload = {
            "extra_json": json.dumps({
                "codex": {
                    "phone_activation": {
                        "phone_number": "+15550001111",
                        "phone_country_name": "United States",
                        "price_amount": "0.02",
                        "price_currency": "USD",
                        "price_limit_fx_rate": "0.148932",
                    }
                }
            })
        }
        content = {
            "_filename": "codex-phone@example.test.json",
            "email": "phone@example.test",
            "phone_activation": {"phone_number": "+15550002222"},
        }

        record = db._codex_content_to_record(content, account_payload)

        self.assertEqual(record["phone"], "+15550002222")
        self.assertEqual(record["phone_country"], "United States")
        self.assertEqual(record["phone_price"], "0.02 USD")
        self.assertEqual(record["phone_price_cny"], "0.134289")

    def test_cpa_callback_falls_back_to_linked_account_metadata(self):
        account_payload = {
            "account_id": "93fa0f15-0d17-46b6-84ec-47bee9411aa2",
            "current_plan_type": "free",
            "plan_type": "free",
            "expires_at": "2027-01-04T01:37:21.817Z",
        }
        content = {
            "_filename": "codex-cpa@example.test-cpa-callback.json",
            "email": "cpa@example.test",
            "type": "codex_cpa_callback",
        }

        record = db._codex_content_to_record(content, account_payload)

        self.assertEqual(record["plan"], "free")
        self.assertEqual(record["account_id"], "93fa0f15-0d17-46b6-84ec-47bee9411aa2")
        self.assertEqual(record["expired"], "2027-01-04T01:37:21.817Z")

    def test_codex_templates_append_cny_price_after_original_price(self):
        root = Path(__file__).resolve().parents[1] / "webui" / "templates"
        for name in ("index.html", "index_legacy.html"):
            content = (root / name).read_text(encoding="utf-8")
            self.assertIn("function _codexPhonePriceDisplay(price, priceCny)", content)
            self.assertIn("r.phone_price_cny", content)
            self.assertIn("original + ' / ' + cny + ' ￥'", content)


if __name__ == "__main__":
    unittest.main()
