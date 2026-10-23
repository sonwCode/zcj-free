# -*- coding: utf-8 -*-
"""OAuth 回调 code 的健壮提取。

借鉴 starmiaoa/chatgpt-register-k12 的 login/login_flow.py：
它在三个层次上提取 code，因为回调 code 不一定出现在地址栏：

  1. URL 查询参数（最常见）；
  2. 页面文本 / HTML / JS 里嵌的回调链接（重定向被拦截时页面会把它渲染出来）；
  3. JSON 响应体里的回调地址。

并且做了两层规范化，这两层很容易漏：
  * HTML 转义：&amp; → &
  * JS 转义：\\u0026 → &、\\/ → /
  * URL 编码可能被套了两层，要反复解码到稳定为止。
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

CALLBACK_HINT = 'localhost:1455/auth/callback'
_URL_RE = re.compile(r'https?://[^\s"\'<>]{10,400}')


def _normalise(value: str) -> str:
    """还原 HTML/JS 转义与多层 URL 编码。"""
    text = html.unescape(str(value or '').strip())
    text = text.replace('\\u0026', '&').replace('\\/', '/').replace('\\u003d', '=')
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def code_from_url(url: str) -> str:
    """从 URL 查询参数里取 code。"""
    text = _normalise(url)
    if not text:
        return ''
    try:
        parsed = parse_qs(urlparse(text).query)
    except Exception:
        return ''
    return str((parsed.get('code') or [''])[0]).strip()


def code_from_text(text: str) -> str:
    """在任意文本（HTML/JS/JSON）里找回调链接并取 code。"""
    body = _normalise(text)
    if not body:
        return ''
    for match in _URL_RE.finditer(body):
        candidate = match.group(0)
        if 'code=' not in candidate:
            continue
        code = code_from_url(candidate)
        if code:
            return code
    # 兜底：直接抓 code=... 片段
    loose = re.search(r'[?&]code=([A-Za-z0-9._~%-]{8,})', body)
    return _normalise(loose.group(1)) if loose else ''


def extract(url: str = '', text: str = '', data=None) -> str:
    """依次尝试 URL → 文本 → 结构化数据。"""
    code = code_from_url(url)
    if code:
        return code
    if text:
        code = code_from_text(text)
        if code:
            return code
    if isinstance(data, dict):
        for key in ('redirect_url', 'continue_url', 'url', 'callback'):
            value = data.get(key)
            if isinstance(value, str) and value:
                code = code_from_url(value) or code_from_text(value)
                if code:
                    return code
    return ''
