# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from core import db


class EmailPoolSourceTests(unittest.TestCase):
    def _storage_patches(self, root: Path) -> dict:
        return {
            "_ACCOUNTS_JSON": root / "accounts.json",
            "_OUTLOOK_JSON": root / "outlook.json",
            "_GENERIC_API_EMAIL_JSON": root / "generic.json",
            "_DOMAIN_EMAIL_JSON": root / "domain.json",
            "_JOBS_JSON": root / "jobs.json",
            "_LEGACY_ACCOUNTS_JSON": root / "legacy-accounts.json",
            "_LEGACY_OUTLOOK_JSON": root / "legacy-outlook.json",
            "_LEGACY_JOBS_JSON": root / "legacy-jobs.json",
            "_LEGACY_SQLITE": root / "legacy.db",
            "_CODEX_DIR": root / "codex_accounts",
            "_CODEX_AGENT_DIR": root / "codex_agent_accounts",
            "_LEGACY_CODEX_EXPORT_STATE": root / "codex-export.json",
            "_SQLITE_READY": False,
            "_SQLITE_READY_PATH": None,
        }

    def test_generic_api_import_keeps_source_for_all_and_filtered_lists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                self.assertEqual(
                    db.import_generic_api_emails([
                        {"email": "generic@example.com", "code_url": "https://mail.example/code"},
                    ]),
                    (1, 0),
                )

                all_items = db.list_email_pool_page(source="all", limit=10)["items"]
                generic_items = db.list_email_pool_page(source="generic_api", limit=10)["items"]
                self.assertEqual(all_items[0]["source"], "generic_api")
                self.assertEqual(generic_items[0]["source"], "generic_api")

    def test_repairs_source_for_legacy_generic_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                db._ensure_sqlite()
                payload = {
                    "id": 1,
                    "email": "legacy-generic@example.com",
                    "code_url": "https://mail.example/legacy-code",
                    "status": "available",
                }
                with closing(db._sqlite_conn()) as conn:
                    conn.execute(
                        "INSERT INTO email_pool(id,email,source,status,archived,created_at,updated_at,payload) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (1, payload["email"], "", "available", 0, "", "", json.dumps(payload)),
                    )
                    conn.commit()

                db._SQLITE_READY = False
                db._SQLITE_READY_PATH = None
                items = db.list_email_pool_page(source="generic_api", limit=10)["items"]
                self.assertEqual(items[0]["source"], "generic_api")

    def test_repairs_source_for_legacy_domain_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                db._ensure_sqlite()
                payload = {
                    "id": 1,
                    "email": "legacy-domain@example.com",
                    "status": "available",
                    "used_at": None,
                }
                with closing(db._sqlite_conn()) as conn:
                    conn.execute(
                        "INSERT INTO email_pool(id,email,source,status,archived,created_at,updated_at,payload) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (1, payload["email"], "", "available", 0, "", "", json.dumps(payload)),
                    )
                    conn.commit()

                db._SQLITE_READY = False
                db._SQLITE_READY_PATH = None
                items = db.list_email_pool_page(source="cloudflare_domain", limit=10)["items"]
                self.assertEqual(items[0]["source"], "cloudflare_domain")

    def test_delete_pool_supports_all_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                db.import_generic_api_emails([
                    {"email": "generic@example.com", "code_url": "https://mail.example/code"},
                ])
                db.import_outlook_accounts([
                    {
                        "email": "outlook@example.com",
                        "password": "password",
                        "client_id": "client",
                        "refresh_token": "refresh",
                    },
                ])
                db.claim_next_domain_email("domain@example.com")

                self.assertTrue(db.delete_email_pool("generic@example.com", source="all"))
                self.assertTrue(db.delete_email_pool("outlook@example.com", source="outlook"))
                self.assertTrue(db.delete_email_pool("domain@example.com", source="cloudflare_domain"))
                self.assertFalse(db.list_email_pool_page(source="all", limit=10)["items"])

    def test_insert_account_links_every_local_pool_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                db.import_generic_api_emails([
                    {"email": "generic-linked@example.com", "code_url": "https://mail.example/code"},
                ])
                db.import_imap_emails([
                    {"email": "imap-linked@example.com", "password": "pw", "server": "imap.example.com", "port": 993},
                ])
                db.claim_next_domain_email("domain-linked@example.com")

                fixtures = (
                    ("generic_api", "generic-linked@example.com", db.get_generic_api_email_by_email),
                    ("imap", "imap-linked@example.com", db.get_imap_email_by_email),
                    ("cloudflare_domain", "domain-linked@example.com", db.get_domain_email_by_email),
                )
                for source, email, getter in fixtures:
                    with self.subTest(source=source):
                        requested_source = "generic-api" if source == "generic_api" else source
                        account_id = db.insert_account(
                            email=email,
                            access_token=f"token-{source}",
                            totp_secret="JBSWY3DPEHPK3PXP",
                            email_source=requested_source,
                        )
                        pool = getter(email)
                        self.assertEqual(pool["status"], "used")
                        self.assertEqual(pool["registered_account_id"], account_id)
                        self.assertEqual(pool["access_token"], f"token-{source}")
                        self.assertTrue(pool["completed_at"])
                        self.assertEqual(pool["totp_secret"], "JBSWY3DPEHPK3PXP")
                        account = db.get_account_by_email(email)
                        self.assertEqual(account["email_source"], source)
                        self.assertEqual(account["original_email_line"], pool["copy_line"] if source != "cloudflare_domain" else email)
                        listed = db.list_email_pool_page(source=source, limit=10)["items"]
                        listed_row = next(item for item in listed if item["email"] == email)
                        self.assertEqual(listed_row["registered_account_id"], account_id)
                        self.assertTrue(listed_row["account_copy_line"])

    def test_domain_claim_is_used_and_unconsumed_release_restores_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                row = db.claim_next_domain_email("pending-domain@example.com")
                self.assertEqual(row["status"], "used")
                self.assertTrue(row["used_at"])
                self.assertTrue(
                    db.release_unconsumed_domain_email(
                        "pending-domain@example.com", note="fixture release"
                    )
                )
                released = db.get_domain_email_by_email("pending-domain@example.com")
                self.assertEqual(released["status"], "available")
                self.assertIsNone(released["used_at"])

    def test_registration_job_recovery_does_not_leave_active_states(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                pending = db.create_job("outlook")
                stopped = db.create_job("outlook")
                db.update_job(stopped["id"], status="running", email="interrupted@example.com")

                account_id = db.insert_account(
                    email="saved@example.com",
                    access_token="saved-token",
                    email_source="outlook",
                )
                saved = db.create_job("outlook")
                db.update_job(
                    saved["id"],
                    status="running",
                    email="saved@example.com",
                    account_id=account_id,
                )

                recovered = db.recover_interrupted_registration_jobs()
                by_id = {item["id"]: item for item in recovered}
                self.assertEqual(by_id[pending["id"]]["status"], "cancelled")
                self.assertEqual(by_id[stopped["id"]]["status"], "stopped")
                self.assertTrue(by_id[saved["id"]]["had_account"])
                self.assertEqual(by_id[saved["id"]]["status"], "partial_success")
                self.assertNotIn(
                    db.get_job(pending["id"])["status"],
                    {"pending", "running", "stopping"},
                )

    def test_claim_skips_excluded_email_for_all_local_pool_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                db.import_outlook_accounts([
                    {"email": "outlook-first@example.com", "password": "pw", "client_id": "cid", "refresh_token": "rt"},
                    {"email": "outlook-second@example.com", "password": "pw", "client_id": "cid", "refresh_token": "rt"},
                ])
                db.import_generic_api_emails([
                    {"email": "generic-first@example.com", "code_url": "https://mail.example/first"},
                    {"email": "generic-second@example.com", "code_url": "https://mail.example/second"},
                ])
                db.import_imap_emails([
                    {"email": "imap-first@example.com", "password": "pw", "server": "imap.example.com", "port": 993},
                    {"email": "imap-second@example.com", "password": "pw", "server": "imap.example.com", "port": 993},
                ])

                outlook = db.claim_next_outlook(exclude_emails=["OUTLOOK-FIRST@EXAMPLE.COM"])
                generic = db.claim_next_generic_api_email(exclude_emails="GENERIC-FIRST@EXAMPLE.COM")
                imap = db.claim_next_imap_email(exclude_emails={"imap-first@example.com"})

                self.assertEqual(outlook["email"], "outlook-second@example.com")
                self.assertEqual(generic["email"], "generic-second@example.com")
                self.assertEqual(imap["email"], "imap-second@example.com")
                self.assertEqual(db.get_outlook_by_email("outlook-first@example.com")["status"], "available")
                self.assertEqual(db.get_generic_api_email_by_email("generic-first@example.com")["status"], "available")
                self.assertEqual(db.get_imap_email_by_email("imap-first@example.com")["status"], "available")

    def test_retry_job_persists_excluded_emails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                source = db.create_job("outlook")
                db.update_job(source["id"], status="failed", email="failed@example.com")
                retry, created = db.create_retry_job(
                    source["id"],
                    job_type="registration",
                    email_source="outlook",
                    excluded_emails=["failed@example.com", "second@example.com"],
                )

                self.assertTrue(created)
                self.assertEqual(
                    retry["excluded_emails"],
                    ["failed@example.com", "second@example.com"],
                )
                self.assertEqual(
                    db.get_job(retry["id"])["excluded_emails"],
                    ["failed@example.com", "second@example.com"],
                )


    def test_retry_job_preserves_explicit_email_source_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **self._storage_patches(root)):
                source = db.create_job("generic_api", email_source_mode="specific")
                db.update_job(source["id"], status="failed", email="failed@example.com")
                retry, created = db.create_retry_job(
                    source["id"],
                    job_type="registration",
                    email_source=source["email_source"],
                    email_source_mode=source["email_source_mode"],
                )

                self.assertTrue(created)
                self.assertEqual(retry["email_source"], "generic_api")
                self.assertEqual(retry["email_source_mode"], "specific")


if __name__ == "__main__":
    unittest.main()
