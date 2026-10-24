# -*- coding: utf-8 -*-
"""结构化封禁判定（只在明确的字段/码上判死，不做文本猜测）。

来源：aBaiFreeGPT 的 `platforms/chatgpt/credential_checks._explicit_ban_code`。

我们原先的判定混着大量文本匹配（"account_deactivated" 之类子串），
好处是能抓到错误信息里的线索，坏处是**误判风险**：页面文案、日志片段、
甚至别的账号的错误都可能被当成封禁证据。

本模块只信结构化数据：
  * JSON 里出现 {"banned": true} / {"deactivated": true} / {"suspended": true}
  * 字符串恰好等于已知封禁码（大小写不敏感）
  * 字符串是 JSON 时递归解析后再判
  * 否则用**严格**正则匹配裸码（`\b(code)\b`）

拿不准就返回空字符串 —— 宁可"未判定"，也不要误杀。
"""
from __future__ import annotations

import json
import re

#: 已知封禁/停用码（与 OpenAI 错误码对齐）
BAN_CODES = frozenset({
    "account_deactivated",
    "account_banned",
    "account_suspended",
    "account_disabled",
    "account_locked",
    "user_deactivated",
    "user_banned",
    "banned",
    "deactivated",
    "suspended",
    "disabled",
})

_BAN_FLAGS = ("banned", "deactivated", "suspended", "disabled")

_BAN_CODE_PATTERN = re.compile(
    r"\b(account_(?:deactivated|banned|suspended|disabled|locked)|user_(?:deactivated|banned))\b",
    re.IGNORECASE,
)


def explicit_ban_code(value) -> str:
    """从结构化数据里取出明确的封禁码；拿不准返回空串。"""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key or "").strip().lower()
            if normalized in _BAN_FLAGS and child is True:
                return "account_" + normalized
            found = explicit_ban_code(child)
            if found:
                return found
        return ""
    if isinstance(value, (list, tuple)):
        for child in value:
            found = explicit_ban_code(child)
            if found:
                return found
        return ""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in BAN_CODES:
        return lowered
    if text[:1] in ("{", "["):
        try:
            return explicit_ban_code(json.loads(text))
        except (TypeError, ValueError):
            return ""
    match = _BAN_CODE_PATTERN.search(text)
    return str(match.group(1) or "").lower() if match else ""


def has_explicit_ban(value) -> bool:
    return bool(explicit_ban_code(value))


# ---------------------------------------------------------------- 响应分类

#: 封号措辞（dango 实测：封号号的 AT 会被一并吊销，拿不到 200，
#: 所以必须从 401/403 的**响应体文本**里找证据，不能只等 200+is_deactivated）
_BAN_TEXT_MARKERS = (
    "account_deactivated",
    "deactivated",
    "suspended",
    "banned",
    "violat",
    "potential abuse",
    "terms of use",
    "policy violation",
)

#: 凭据失效（可重登恢复），不是封号
_REVOKED_TEXT_MARKERS = (
    "invalidated oauth token",
    "invalid oauth token",
    "token_revoked",
    "token revoked",
    "invalid api key",
)

BANNED = "banned"
REVOKED = "revoked"
EXPIRED = "expired"
UNKNOWN = "unknown"


def classify_token_failure(status_code, body) -> dict:
    """把 401/403 之类的失败响应归类。

    返回 {"kind": banned|revoked|expired|unknown, "reason": str, "ban_code": str}

    顺序很重要：**结构化封禁码优先**，其次是封号措辞，
    然后是吊销措辞——否则"invalidated oauth token"会被误判成封号，
    导致把还能重登救回来的号直接删掉。
    """
    text = str(body or "")
    code = explicit_ban_code(text)
    if code:
        return {"kind": BANNED, "reason": code, "ban_code": code}

    lowered = text.lower()
    for marker in _BAN_TEXT_MARKERS:
        if marker in lowered:
            return {"kind": BANNED, "reason": marker, "ban_code": ""}
    for marker in _REVOKED_TEXT_MARKERS:
        if marker in lowered:
            return {"kind": REVOKED, "reason": marker, "ban_code": ""}
    if "expired" in lowered or "exp" in lowered and "token" in lowered:
        return {"kind": EXPIRED, "reason": "token expired", "ban_code": ""}
    try:
        status = int(status_code)
    except Exception:
        status = 0
    return {"kind": UNKNOWN, "reason": "http-%s" % (status or "?"), "ban_code": ""}


def is_banned_response(status_code, body) -> bool:
    return classify_token_failure(status_code, body)["kind"] == BANNED
