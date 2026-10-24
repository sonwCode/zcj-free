# -*- coding: utf-8 -*-
"""封禁判定与刷新分类的离线测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.ban_detect import explicit_ban_code, has_explicit_ban
from core.refresh_taxonomy import BANNED, OK, RELOGIN, RENEW, UNKNOWN, classify_refresh, needs_relogin


def test_ban_flag_true():
    assert has_explicit_ban({"account": {"deactivated": True}}) is True
    assert explicit_ban_code({"banned": True}) == "account_banned"


def test_ban_flag_false_not_ban():
    assert has_explicit_ban({"deactivated": False}) is False


def test_ban_code_exact_string():
    assert explicit_ban_code("account_deactivated") == "account_deactivated"
    assert explicit_ban_code("ACCOUNT_BANNED") == "account_banned"


def test_ban_inside_json_string():
    raw = '{"error": {"code": "account_suspended"}}'
    assert explicit_ban_code(raw) == "account_suspended"


def test_free_text_not_treated_as_ban():
    # 普通文案不该被当成封禁证据
    assert has_explicit_ban("please contact support if you believe this is an error") is False
    assert has_explicit_ban({"message": "rate limited"}) is False


def test_refresh_403_means_relogin():
    r = classify_refresh(403, {}, "forbidden")
    assert r["kind"] == RELOGIN


def test_refresh_400_invalid_grant_means_renew():
    assert classify_refresh(400, {"error": "invalid_grant"})["kind"] == RENEW
    assert classify_refresh(400, {"error": "invalid_token"})["kind"] == RENEW


def test_refresh_400_description_expired():
    r = classify_refresh(400, {"error_description": "refresh token expired"})
    assert r["kind"] == RENEW


def test_refresh_banned_wins():
    assert classify_refresh(400, {"deactivated": True})["kind"] == BANNED


def test_refresh_ok():
    assert classify_refresh(200, {"access_token": "x"})["kind"] == OK


def test_refresh_unknown():
    assert classify_refresh(500, {}, "")["kind"] == UNKNOWN


def test_needs_relogin_helper():
    assert needs_relogin(403) is True
    assert needs_relogin(200) is False
