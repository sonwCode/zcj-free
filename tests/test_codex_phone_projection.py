import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
