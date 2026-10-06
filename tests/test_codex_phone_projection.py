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


if __name__ == "__main__":
    unittest.main()
