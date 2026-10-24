# -*- coding: utf-8 -*-
"""本地判断 access_token 是否已过期（不发网络请求）。

来源：上游 myfanhua 的 `core/account_inspection_service.token_needs_refresh`。

价值：我们现在查活会对**所有**账号发一次 /me。几千个号时既慢又费代理，
还会因为无谓请求增加被风控的概率。其实 JWT 里就写着 exp——
先本地判过期，只对真过期的发请求。
"""
from __future__ import annotations

import base64
import json
import math
import time


def _normalize(token: str) -> str:
    t = str(token or '').strip()
    if t.lower().startswith('bearer '):
        t = t[7:].strip()
    return t


def decode_jwt_payload_unverified(token: str) -> dict:
    """不验签解码 JWT payload；失败返回空字典。"""
    t = _normalize(token)
    parts = t.split('.')
    if len(parts) != 3:
        return {}
    seg = parts[1]
    pad = '=' * (-len(seg) % 4)
    try:
        raw = base64.urlsafe_b64decode(seg + pad)
        data = json.loads(raw.decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def token_expiry(token: str) -> float | None:
    """返回 exp（纪元秒）；无法解析返回 None。"""
    payload = decode_jwt_payload_unverified(token)
    exp = payload.get('exp')
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    try:
        value = float(exp)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def token_needs_refresh(token: str, now: float | None = None) -> bool:
    """token 是否需要刷新。

    保守策略：解析不出来（非 JWT、无 exp、非法值）一律当作需要刷新，
    避免因为"看起来有效"而漏掉真正过期的号。
    """
    t = _normalize(token)
    if not t:
        return True
    exp = token_expiry(t)
    if exp is None:
        return True
    try:
        return exp <= (time.time() if now is None else float(now))
    except (TypeError, ValueError, OverflowError):
        return True


def seconds_until_expiry(token: str, now: float | None = None) -> float | None:
    exp = token_expiry(token)
    if exp is None:
        return None
    return exp - (time.time() if now is None else float(now))
