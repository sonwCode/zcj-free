# -*- coding: utf-8 -*-
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from core import db
from core import registration_service
from webui.app import create_app


class SmsPoolDashboardApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.startup_patches = ExitStack()
        for owner, name in (
            (registration_service, "recover_interrupted_jobs"),
            (db, "recover_interrupted_plan_checks"),
            (db, "recover_interrupted_extract_links"),
            (db, "recover_interrupted_live_checks"),
            (db, "recover_interrupted_codex_oauth_checks"),
            (db, "recover_interrupted_codex_agents"),
            (db, "recover_interrupted_totp_setups"),
            (db, "recover_interrupted_email_changes"),
        ):
            cls.startup_patches.enter_context(patch.object(owner, name, return_value=0))
        cls.client = create_app(auth_code="test-auth").test_client()

    @classmethod
    def tearDownClass(cls):
        cls.startup_patches.close()

    def _get(self, path):
        return self.client.get(path, headers={"X-Auth-Code": "test-auth"})

    def test_reservations_are_paginated_with_total(self):
        with patch("core.sms_pool_store.list_reservations", return_value=[{"id": 21}]) as list_rows, patch(
            "core.sms_pool_store.count_reservations", return_value=41
        ):
            response = self._get("/api/sms-pool/reservations?limit=20&offset=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"], [{"id": 21}])
        self.assertEqual(response.get_json()["total"], 41)
        list_rows.assert_called_once_with(statuses=None, limit=20, offset=20)

    def test_invalid_reservation_page_is_a_client_error(self):
        with patch("core.sms_pool_store.list_reservations") as list_rows:
            response = self._get("/api/sms-pool/reservations?limit=NaN&offset=NaN")
        self.assertEqual(response.status_code, 400)
        self.assertIn("整数", response.get_json()["error"])
        list_rows.assert_not_called()

    def test_events_are_paginated_by_reservation(self):
        with patch("core.sms_pool_store.recent_reservation_event_summaries", return_value=[{"reservation_id": 7}]) as list_rows, patch(
            "core.sms_pool_store.count_reservation_event_summaries", return_value=25
        ):
            response = self._get("/api/sms-pool/events?limit=10&offset=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"], [{"reservation_id": 7}])
        self.assertEqual(response.get_json()["total"], 25)
        list_rows.assert_called_once_with(limit=10, offset=10)

    def test_invalid_event_page_is_a_client_error(self):
        with patch("core.sms_pool_store.recent_reservation_event_summaries") as list_rows:
            response = self._get("/api/sms-pool/events?offset=NaN")
        self.assertEqual(response.status_code, 400)
        list_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
