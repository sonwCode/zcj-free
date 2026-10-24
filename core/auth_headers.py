# -*- coding: utf-8 -*-
"""统一请求头：client hints / Sec-Fetch / 账号接口头。

来源：dango-register 的 `_navigation_headers()` 与 Plus 检测的"请求头补齐"。

它实测出来的两件事：

1. **必须发 client hints**。自称 Chrome 却不带 `sec-ch-ua`，
   Cloudflare 一眼假：裸头 1/5 通过，补齐后 5/5。
   反过来，"chrome 族被 CF 拦"是**误判**——safari/firefox 本来就不该发 CH。
2. **导航要带 Sec-Fetch-\***。三处导航原本各自手拼头，后两处连
   `Sec-Fetch-*` 都没有；A/B 结果：裸头 2/6 会话正常、3 次 409；
   补齐后 5/6、**409 = 0**。

另外账号级接口要带 `Origin` / `Referer` / `ChatGPT-Account-ID` / `OAI-Device-Id`：
带 `Authorization` 却不带 `Origin/Referer` 是典型非浏览器特征。
"""
from __future__ import annotations

import base64
import json

#: 与 impersonate 对齐的默认 Chrome 版本（换指纹时必须同步，否则自相矛盾）
DEFAULT_CHROME_VERSION = "146"

_SEC_CH_UA = {
    "146": '"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="99"',
    "142": '"Chromium";v="142", "Google Chrome";v="142", "Not?A_Brand";v="99"',
    "136": '"Chromium";v="136", "Google Chrome";v="136", "Not?A_Brand";v="99"',
}


def _chrome_version(version: str | None) -> str:
    v = str(version or '').strip() or DEFAULT_CHROME_VERSION
    return v if v in _SEC_CH_UA else DEFAULT_CHROME_VERSION


def client_hint_headers(user_agent: str = '', platform: str = '"Windows"',
                        version: str | None = None) -> dict:
    """返回 sec-ch-ua 系列头（与 UA 必须一致）。"""
    chrome = _chrome_version(version)
    major = chrome
    return {
        "sec-ch-ua": _SEC_CH_UA[chrome],
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform,
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"%s.0.0.0"' % major,
        "sec-ch-ua-model": '""',
    }


def navigation_headers(*, url: str = 'https://chatgpt.com/', referer: str = '',
                       dest: str = 'document', mode: str = 'navigate',
                       site: str = 'same-origin', user_agent: str = '',
                       version: str | None = None) -> dict:
    """导航级请求头：client hints + Sec-Fetch-*。"""
    headers = client_hint_headers(user_agent=user_agent, version=version)
    headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Sec-Fetch-Dest": dest,
        "Sec-Fetch-Mode": mode,
        "Sec-Fetch-Site": site,
        "Sec-Fetch-User": "?1" if mode == 'navigate' else "",
        "Upgrade-Insecure-Requests": "1",
    })
    if not headers["Sec-Fetch-User"]:
        headers.pop("Sec-Fetch-User")
    if referer:
        headers["Referer"] = referer
    headers["Origin"] = _origin_of(url) if dest != 'document' else "https://chatgpt.com"
    return {k: v for k, v in headers.items() if v}


def _origin_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ''))
    if parsed.scheme and parsed.netloc:
        return '%s://%s' % (parsed.scheme, parsed.netloc)
    return 'https://chatgpt.com'


def account_id_from_token(token: str) -> str:
    """从 access_token 的 JWT 里取 chatgpt account id（不验签）。"""
    t = str(token or '').strip()
    if t.lower().startswith('bearer '):
        t = t[7:].strip()
    parts = t.split('.')
    if len(parts) != 3:
        return ''
    seg = parts[1]
    pad = '=' * (-len(seg) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(seg + pad).decode('utf-8'))
    except Exception:
        return ''
    if not isinstance(payload, dict):
        return ''
    for key in ('chatgpt_account_id', 'account_id'):
        value = payload.get(key)
        if value:
            return str(value)
    auth = payload.get('https://api.openai.com/auth')
    if isinstance(auth, dict):
        value = auth.get('chatgpt_account_id')
        if value:
            return str(value)
    return ''


def account_api_headers(token: str, *, device_id: str = '',
                        url: str = 'https://chatgpt.com/backend-api/me',
                        version: str | None = None) -> dict:
    """账号级接口头：补齐 Origin / Referer / 账号 id / 设备 id。"""
    headers = {
        'Authorization': 'Bearer ' + str(token or '').strip(),
        'Accept': 'application/json',
        'Origin': 'https://chatgpt.com',
        'Referer': 'https://chatgpt.com/',
        'Content-Type': 'application/json',
    }
    account_id = account_id_from_token(token)
    if account_id:
        headers['ChatGPT-Account-ID'] = account_id
    if device_id:
        headers['OAI-Device-Id'] = str(device_id)
    headers.update(client_hint_headers(version=version))
    return headers
