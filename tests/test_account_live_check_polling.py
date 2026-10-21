import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db


class AccountLiveCheckPollingTests(unittest.TestCase):
    def test_light_snapshot_exposes_live_check_and_revision_tracks_transitions(self):
        rows = [{
            "id": 1,
            "email": "live-check@example.test",
            "live_check_status": "queued",
            "live_check_ok": False,
            "live_check_error": None,
            "live_checked_at": None,
        }]

        def page(*_args, **_kwargs):
            return copy.deepcopy(rows), 1, "same-second"

        with patch.object(db, "_query_collection_page", side_effect=page):
            queued = db.list_account_plan_check_statuses(limit=10)
            queued_item = queued["items"][0]
            self.assertEqual(queued_item["live_check_status"], "queued")
            self.assertIs(queued_item["live_check_ok"], False)

            rows[0].update({
                "live_check_status": "live",
                "live_check_ok": True,
                "live_check_error": None,
                "live_checked_at": "2026-10-19T10:00:00",
            })
            finished = db.list_account_plan_check_statuses(limit=10)

        self.assertEqual(finished["items"][0]["live_check_status"], "live")
        self.assertIs(finished["items"][0]["live_check_ok"], True)
        self.assertNotEqual(queued["revision"], finished["revision"])

    def test_account_list_renders_network_failure_as_unknown_not_dead(self):
        template = Path(__file__).resolve().parents[1] / "webui" / "templates" / "index.html"
        content = template.read_text(encoding="utf-8")
        renderer = content.split("function _tokenCellV2(r)", 1)[1].split("function _accountsV2MoreMenu(r)", 1)[0]
        self.assertIn("live === 'network_unavailable'", renderer)
        self.assertIn("网络异常·未判定", renderer)
        self.assertIn("检查失败·未判定", renderer)
        self.assertIn("live === 'deactivated'", renderer)


if __name__ == "__main__":
    unittest.main()
