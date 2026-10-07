# -*- coding: utf-8 -*-
"""Browser Use Cloud client with explicit browser-session ownership."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests

from config import browser_use as _cfg

logger = logging.getLogger(__name__)


@dataclass
class BrowserUseSession:
    connect_url: str
    api_key_present: bool
    proxy_country_code: str = ""
    profile_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""


class BrowserUseClient:
    """Create, connect to, and explicitly stop Browser Use cloud browsers."""

    def __init__(self, api_key: str | None = None):
        self.api_key = str(api_key if api_key is not None else getattr(_cfg, "BROWSER_USE_API_KEY", "") or "").strip()
        self._close_attempted: set[str] = set()

    def _api_key(self) -> str:
        value = self.api_key
        if not value:
            raise RuntimeError("BROWSER_USE_API_KEY 为空。请在 .env 或 WebUI 配置页填写 Browser Use API Key。")
        return value

    def require_api_key(self) -> str:
        return self._api_key()

    @staticmethod
    def _api_v4_base() -> str:
        base = str(getattr(_cfg, "BROWSER_USE_API_BASE", "") or "https://api.browser-use.com/api/v4").rstrip("/")
        for suffix in ("/api/v2", "/api/v3"):
            if base.endswith(suffix):
                return base[: -len(suffix)] + "/api/v4"
        if not base.endswith("/api/v4"):
            return base + "/api/v4"
        return base

    def _headers(self) -> dict[str, str]:
        return {
            "X-Browser-Use-API-Key": self._api_key(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _open_api_v4_session(self) -> BrowserUseSession:
        timeout = max(1, min(240, int(getattr(_cfg, "BROWSER_USE_SESSION_TIMEOUT", 240) or 240)))
        country = str(getattr(_cfg, "BROWSER_USE_PROXY_COUNTRY_CODE", "") or "").strip().lower()
        use_proxy = bool(getattr(_cfg, "BROWSER_USE_USE_PROXY", True))
        effective_country = country if use_proxy else ""
        profile_id = str(getattr(_cfg, "BROWSER_USE_PROFILE_ID", "") or "").strip()
        payload: dict[str, Any] = {"timeout": timeout}
        if effective_country:
            payload["proxyCountryCode"] = effective_country
        if profile_id:
            payload["profileId"] = profile_id

        response = requests.post(
            f"{self._api_v4_base()}/browsers",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        try:
            data = response.json()
        except Exception:
            data = {"text": response.text[:1000]}
        if response.status_code >= 400:
            raise RuntimeError(f"Browser Use create browser HTTP {response.status_code}: {data}")
        if not isinstance(data, dict):
            raise RuntimeError(f"Browser Use create browser 响应不是对象: {data!r}")
        body = data.get("data") if isinstance(data.get("data"), dict) else data
        session_id = str(body.get("id") or body.get("sessionId") or "").strip()
        connect_url = str(body.get("cdpUrl") or body.get("cdp_url") or "").strip()
        if not session_id or not connect_url:
            if session_id:
                for attempt in range(2):
                    try:
                        self.close_browser_session(session_id)
                        break
                    except Exception as exc:
                        logger.warning(
                            "[BrowserUse] 回滚 browser session 失败：session_id=%s attempt=%s/2 %s: %s",
                            session_id,
                            attempt + 1,
                            type(exc).__name__,
                            str(exc)[:180],
                        )
            raise RuntimeError(f"Browser Use create browser 缺少 id/cdpUrl: {data}")
        logger.info("[BrowserUse] browser session 已创建：session_id=%s", session_id)
        return BrowserUseSession(
            connect_url=connect_url,
            api_key_present=True,
            proxy_country_code=effective_country,
            profile_id=profile_id,
            raw=dict(body),
            session_id=session_id,
        )

    def _open_legacy_cdp_session(self) -> BrowserUseSession:
        api_key = self._api_key()
        base = str(getattr(_cfg, "BROWSER_USE_CDP_BASE", "") or "wss://connect.browser-use.com").strip()
        country = str(getattr(_cfg, "BROWSER_USE_PROXY_COUNTRY_CODE", "") or "").strip().lower()
        use_proxy = bool(getattr(_cfg, "BROWSER_USE_USE_PROXY", True))
        profile_id = str(getattr(_cfg, "BROWSER_USE_PROFILE_ID", "") or "").strip()
        timeout_minutes = max(1, min(240, int(getattr(_cfg, "BROWSER_USE_SESSION_TIMEOUT", 240) or 240)))
        query: dict[str, str] = {"apiKey": api_key, "timeout": str(timeout_minutes)}
        if use_proxy and country:
            query["proxyCountryCode"] = country
        if profile_id:
            query["profileId"] = profile_id
        extra = getattr(_cfg, "BROWSER_USE_EXTRA_QUERY", {}) or {}
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value is not None and str(value).strip():
                    query[str(key)] = str(value)
        separator = "&" if "?" in base else "?"
        safe_query = dict(query)
        safe_query["apiKey"] = api_key[:6] + "***"
        return BrowserUseSession(
            connect_url=f"{base}{separator}{urlencode(query)}",
            api_key_present=True,
            proxy_country_code=country if use_proxy else "",
            profile_id=profile_id,
            raw={"mode": "legacy_cdp_url", "query": safe_query, "base": base},
        )

    def build_connect_url(self) -> BrowserUseSession:
        """Compatibility wrapper for callers that explicitly need legacy CDP."""
        return self._open_legacy_cdp_session()

    def open_session(self) -> BrowserUseSession:
        mode = str(getattr(_cfg, "BROWSER_USE_CONNECT_MODE", "api_v4") or "api_v4").strip().lower()
        if mode in {"api_v4", "v4", "rest"}:
            return self._open_api_v4_session()
        if mode in {"cdp_url", "cdp", "websocket", "ws", "sdk"}:
            return self._open_legacy_cdp_session()
        raise RuntimeError(
            f"不支持的 BROWSER_USE_CONNECT_MODE={mode!r}，"
            "可选 api_v4 / cdp_url"
        )

    def close_browser_session(self, session_id: str) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id or session_id in self._close_attempted:
            return {"ok": True, "already_attempted": True}
        response = requests.patch(
            f"{self._api_v4_base()}/browsers/{session_id}",
            headers=self._headers(),
            json={"action": "stop"},
            timeout=20,
        )
        try:
            data = response.json()
        except Exception:
            data = {"text": response.text[:1000]}
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(f"Browser Use stop browser HTTP {response.status_code}: {data}")
        self._close_attempted.add(session_id)
        logger.info("[BrowserUse] browser session 已停止：session_id=%s", session_id)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
