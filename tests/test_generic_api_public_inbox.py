# -*- coding: utf-8 -*-
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from core.generic_api_mail_client import (
    GenericApiEmailAccount,
    GenericApiMailError,
    _extract_structured_api_code,
    _public_inbox_latest_code_url,
    fetch_latest_otp,
)
from core.db import _normalize_generic_api_code_url


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.urls = []
        self.proxies = {}
        self.trust_env = True

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return _Response({
            "mailbox": {"address": "inbox-0141-d678@071898.7bcb28.221wx.com"},
            "messages": [{
                "id": "msg_01",
                "receivedAt": "2026-09-06T12:00:00.000Z",
                "subject": "ChatGPT の一時的な認証コード",
                # 该站点日文邮件 verificationCodes 可能为空，但页面 preview 已有验证码。
                "verificationCodes": [],
                "preview": "この一時検証コードを入力して続行してください: 739201 ChatGPT",
                "fromAddress": "service@example.com",
            }],
        })


class _StructuredSession:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []
        self.proxies = {}
        self.trust_env = True

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return _Response(self.payload)


class GenericApiPublicInboxTests(unittest.TestCase):
    def test_otp_diagnostics_do_not_log_code_values(self):
        source = (Path(__file__).parents[1] / "core" / "generic_api_mail_client.py").read_text(encoding="utf-8")
        self.assertNotIn("OTP={code}", source)
        self.assertNotIn("OTP={best_otp}", source)
        self.assertNotIn("structured API 跳过旧验证码: code=%s", source)
        self.assertNotIn("inline messages 页面提取到 OTP=%s", source)
        self.assertIn("OTP：length=", source)

    def test_structured_message_list_filters_old_and_selects_newest(self):
        payload = {
            "items": [
                {
                    "id": "message-older-after",
                    "receivedAt": 2600,
                    "subject": "Your temporary ChatGPT login code",
                    "bodyPreview": "Enter this temporary verification code to continue: 123456",
                    "verificationCode": "123456",
                },
                {
                    "id": "message-newest",
                    "receivedAt": 3000,
                    "subject": "Your temporary ChatGPT login code",
                    "bodyPreview": "Enter this temporary verification code to continue: 654321",
                    "verificationCode": "654321",
                },
                {
                    "id": "message-old",
                    "receivedAt": 2000,
                    "subject": "Your temporary ChatGPT login code",
                    "bodyPreview": "Enter this temporary verification code to continue: 111111",
                    "verificationCode": "111111",
                },
            ]
        }
        parsed = _extract_structured_api_code(json.dumps(payload), after_ts=2500)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], "654321")
        self.assertEqual(parsed[1]["mail_id"], "message-newest")
        old_only = {"items": [payload["items"][2]]}
        self.assertIsNone(_extract_structured_api_code(json.dumps(old_only), after_ts=2500))

        email = "inbox@example.com"
        account = GenericApiEmailAccount(
            email=email,
            code_url="https://mail.example/v1/pickup?email=inbox@example.com&token=service-token",
        )
        session = _StructuredSession(payload)
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client.requests.Session", return_value=session):
            code = fetch_latest_otp(
                email,
                after_ts=2500,
                max_wait=2,
                poll_interval=0.01,
                settle_seconds=0,
            )
        self.assertEqual(code, "654321")
        self.assertEqual(len(session.urls), 1)
        self.assertIn("_otp_poll=", session.urls[0])

        old_session = _StructuredSession(old_only)
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client.requests.Session", return_value=old_session):
            with self.assertRaises(GenericApiMailError):
                fetch_latest_otp(
                    email,
                    after_ts=2500,
                    max_wait=0.05,
                    poll_interval=0.01,
                    settle_seconds=0,
                )
        self.assertGreaterEqual(len(old_session.urls), 1)

    def test_fetch_latest_otp_excludes_previously_submitted_code(self):
        email = "inbox@example.com"
        account = GenericApiEmailAccount(email=email, code_url="https://mail.example/v1/pickup?email=inbox@example.com&token=service-token")
        payload = {"items": [{
            "id": "old-message", "receivedAt": 4000,
            "subject": "Your temporary ChatGPT login code",
            "bodyPreview": "Enter this temporary verification code to continue: 654321",
            "verificationCode": "654321",
        }]}
        session = _StructuredSession(payload)
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client.requests.Session", return_value=session):
            with self.assertRaises(GenericApiMailError):
                fetch_latest_otp(email, after_ts=3000, exclude_codes={"654321"}, max_wait=0.05, poll_interval=0.01, settle_seconds=0)


    def test_remail_console_pickup_page_is_normalized_to_json_endpoint(self):
        page_url = (
            "https://remail.aishop6.com/pickup?"
            "email=inbox@example.com&token=service-token"
        )
        self.assertEqual(
            _normalize_generic_api_code_url(page_url),
            "https://remail.aishop6.com/v1/pickup?"
            "email=inbox@example.com&token=service-token",
        )

    def test_public_link_is_converted_to_latest_code_api(self):
        self.assertEqual(
            _public_inbox_latest_code_url("https://mail.knm03.com/i/HTOJyWzFuVXC"),
            "https://mail.knm03.com/api/public/inboxes/HTOJyWzFuVXC/latest-code",
        )

    def test_direct_latest_code_api_is_also_accepted(self):
        url = "https://mail.knm03.com/api/public/inboxes/HTOJyWzFuVXC/latest-code"
        self.assertEqual(_public_inbox_latest_code_url(url), url)

    def test_fetch_latest_otp_uses_public_inbox_api(self):
        email = "inbox-0141-d678@071898.7bcb28.221wx.com"
        account = GenericApiEmailAccount(
            email=email,
            code_url="https://mail.knm03.com/i/HTOJyWzFuVXC",
        )
        session = _Session()
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client.requests.Session", return_value=session):
            code = fetch_latest_otp(
                email,
                after_ts=1788690000,
                max_wait=2,
                poll_interval=0.01,
                settle_seconds=0,
            )
        self.assertEqual(code, "739201")
        self.assertEqual(len(session.urls), 1)
        self.assertTrue(session.urls[0].startswith(
            "https://mail.knm03.com/api/public/inboxes/HTOJyWzFuVXC?"
        ))
        self.assertFalse(session.trust_env)

    def test_fetch_latest_otp_applies_dedicated_proxy_route(self):
        email = "inbox-0141-d678@071898.7bcb28.221wx.com"
        account = GenericApiEmailAccount(email=email, code_url="https://mail.knm03.com/i/token")
        session = _Session()
        proxy = "socks5://user:secret@127.0.0.1:7897"
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client._email_cfg.GENERIC_API_PROXY", proxy), \
             patch("core.generic_api_mail_client.requests.Session", return_value=session):
            code = fetch_latest_otp(email, max_wait=2, poll_interval=0.01, settle_seconds=0)
        self.assertEqual(code, "739201")
        self.assertEqual(session.proxies, {"http": proxy, "https": proxy})

    def test_proxy_connection_error_falls_back_to_direct(self):
        email = "inbox-0141-d678@071898.7bcb28.221wx.com"
        account = GenericApiEmailAccount(email=email, code_url="https://mail.knm03.com/i/token")
        proxy_session = _Session()
        direct_session = _Session()

        def proxy_failure(_url, **_kwargs):
            import requests
            raise requests.ConnectionError("proxy reset")

        proxy_session.get = proxy_failure
        with patch("core.generic_api_mail_client.get_account_context", return_value=account), \
             patch("core.generic_api_mail_client._email_cfg.GENERIC_API_PROXY", "socks5://127.0.0.1:7897"), \
             patch("core.generic_api_mail_client.requests.Session", side_effect=[proxy_session, direct_session]):
            code = fetch_latest_otp(email, max_wait=2, poll_interval=0.01, settle_seconds=0)
        self.assertEqual(code, "739201")
        self.assertEqual(proxy_session.proxies["https"], "socks5://127.0.0.1:7897")
        self.assertEqual(direct_session.proxies, {})
        self.assertFalse(direct_session.trust_env)


if __name__ == "__main__":
    unittest.main()
