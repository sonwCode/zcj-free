# -*- coding: utf-8 -*-
"""重放浏览器注册过程中的 Datadog RUM beacon。

浏览器注册会往 browser-intake-datadoghq.com 发上百个 beacon（会话、交互、
性能事件）。纯协议注册一个都不发，这在风控看来是明显的"非浏览器"信号。

这里把 HAR 里录到的 beacon 按序重放一遍，带上协议会话自己的 Cookie。
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_HOST_MARK = 'browser-intake-datadoghq.com'
_SKIP_HEADERS = {'host', 'content-length', 'connection', 'accept-encoding'}


def load_beacons(har_path: str, limit: int = 60) -> list[dict]:
    """从 HAR 里取出 datadog beacon 的 method/url/headers/body。"""
    path = Path(str(har_path or ''))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.warning('[DD重放] HAR 解析失败：%s', har_path)
        return []
    out: list[dict] = []
    for entry in data.get('log', {}).get('entries', []):
        req = entry.get('request') or {}
        url = str(req.get('url') or '')
        if _HOST_MARK not in url:
            continue
        headers = {h['name'].lower(): h['value'] for h in (req.get('headers') or [])}
        headers = {k: v for k, v in headers.items() if k not in _SKIP_HEADERS}
        body = (req.get('postData') or {}).get('text')
        out.append({
            'method': str(req.get('method') or 'POST').upper(),
            'url': url,
            'headers': headers,
            'body': body,
        })
        if len(out) >= limit:
            break
    return out


def replay(session, har_path: str, limit: int = 60, delay_range: tuple[float, float] = (0.05, 0.25)) -> dict:
    """用协议会话重放 beacon；失败不影响注册主流程。"""
    beacons = load_beacons(har_path, limit=limit)
    if not beacons:
        return {'ok': False, 'sent': 0, 'error': '没有可重放的 beacon'}
    curl_session = getattr(session, 'session', None)
    if curl_session is None:
        return {'ok': False, 'sent': 0, 'error': '会话不可用'}
    sent = 0
    for item in beacons:
        try:
            resp = curl_session.request(
                item['method'],
                item['url'],
                headers=item['headers'] or None,
                data=item['body'].encode('utf-8') if item['body'] else None,
                timeout=8,
            )
            if int(getattr(resp, 'status_code', 0) or 0) < 400:
                sent += 1
        except Exception:
            continue
        time.sleep(random.uniform(*delay_range))
    logger.info('[DD重放] 已重放 %d/%d 个 beacon', sent, len(beacons))
    return {'ok': sent > 0, 'sent': sent, 'total': len(beacons)}
