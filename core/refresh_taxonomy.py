# -*- coding: utf-8 -*-
"""刷新令牌响应分类：决定"重登"还是"续期"还是"真封禁"。

来源：aBaiFreeGPT 的 `_is_invalid_refresh_response`。

我们此前把刷新失败一律当"号废了"，但实际有三种完全不同的处置：
  * 403                → 凭据过期/被吊销，**重新走协议登录**可拿到新 AT（不是封号）；
  * 400 + invalid_grant / invalid_token
    （或描述里 refresh + invalid/expired/revoked）→ 需要**重新授权**；
  * 结构化封禁标记     → 才是真封号。

区分这三者能避免"把一次性失效的号当死号删掉"。
"""
from __future__ import annotations

from core.ban_detect import explicit_ban_code

RELOGIN = "relogin"
RENEW = "renew"
BANNED = "banned"
OK = "ok"
UNKNOWN = "unknown"

_INVALID_GRANTS = frozenset({"invalid_grant", "invalid_token"})
_EXPIRY_MARKERS = ("invalid", "expired", "revoked")


def classify_refresh(status_code: int, payload=None, text: str = "") -> dict:
    """给刷新响应归类，返回 {kind, reason}。"""
    data = payload if isinstance(payload, dict) else {}
    body = str(text or "")

    ban = explicit_ban_code(data) or explicit_ban_code(body)
    if ban:
        return {"kind": BANNED, "reason": ban}

    try:
        code = int(status_code)
    except Exception:
        code = 0

    if code == 403:
        return {"kind": RELOGIN, "reason": "http_403_stale_credential"}

    if code == 400:
        error = str(data.get("error") or "").strip().lower()
        description = str(
            data.get("error_description") or data.get("message") or body or ""
        ).lower()
        if error in _INVALID_GRANTS:
            return {"kind": RENEW, "reason": error}
        if "refresh" in description and any(m in description for m in _EXPIRY_MARKERS):
            return {"kind": RENEW, "reason": "refresh_description_expired"}
        return {"kind": UNKNOWN, "reason": "http_400_unclassified"}

    if 200 <= code < 300:
        return {"kind": OK, "reason": ""}

    if code in (401,):
        return {"kind": RELOGIN, "reason": "http_401"}

    return {"kind": UNKNOWN, "reason": "http_%s" % (code or "?")}


def needs_relogin(status_code: int, payload=None, text: str = "") -> bool:
    return classify_refresh(status_code, payload, text)["kind"] in (RELOGIN, RENEW)
