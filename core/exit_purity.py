# -*- coding: utf-8 -*-
"""出口纯净度检测（数据中心标记 / 代理标记 / IPv6 泄漏）。

借鉴 HengXin666/gpt-web-cf-make 的 proxy_check_service：
它不只看"能不能连"，还给出口打分：

    IP 为数据中心      -20
    IP 被标记为代理    -15
    TLS 指纹异常       -15
    IPv6 泄露          -5
    DNS 泄露           -10
    各端点不可达       每个 -10

为什么重要：
  我们的 proxy_edge_probe 只看 HTTP 状态码。一个机房出口今天可能全部返回 200，
  但明天账号就会被批量封——因为 hosting=True 本身就是最强的风控信号。
  两者是**不同轴**：edge 判"通不通"，purity 判"脏不脏"。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

IP_API = ('http://ip-api.com/json/?fields=status,message,country,city,isp,as,query,'
          'proxy,mobile,hosting,org')
IPIFY_V4 = 'https://api.ipify.org?format=text'
IPIFY_V6 = 'https://api64.ipify.org?format=json'


def _session(proxy: str):
    import requests

    session = requests.Session()
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    return session


def ip_profile(proxy: str, timeout: float = 8.0) -> dict:
    """查询出口 IP 画像（是否机房、是否被标记代理、AS、国家）。"""
    try:
        session = _session(proxy)
        resp = session.get(IP_API, timeout=timeout)
        data = resp.json()
    except Exception as exc:
        return {'error': '%s: %s' % (type(exc).__name__, str(exc)[:100])}
    if not isinstance(data, dict) or data.get('status') != 'success':
        return {'error': str((data or {}).get('message') or '查询失败')[:100]}
    return data


def ipv6_leak(proxy: str, timeout: float = 6.0) -> dict:
    """IPv4 与 IPv6 查询是否指向同一个出口；不同则视为 IPv6 泄漏。"""
    out = {'v4': '', 'v6': '', 'leak': False, 'error': ''}
    try:
        session = _session(proxy)
        out['v4'] = str(session.get(IPIFY_V4, timeout=timeout).text or '').strip()
        raw = session.get(IPIFY_V6, timeout=timeout).json()
        out['v6'] = str((raw or {}).get('ip') or '').strip()
    except Exception as exc:
        out['error'] = '%s: %s' % (type(exc).__name__, str(exc)[:80])
        return out
    if out['v6'] and out['v4'] and out['v6'] != out['v4']:
        out['leak'] = True
    return out


def score(profile: dict, leak: dict | None = None) -> dict:
    """按参考实现的权重打分，返回 score/grade/reasons。"""
    total = 100
    reasons = []
    data = profile if isinstance(profile, dict) else {}
    if data.get('error'):
        return {'score': 0, 'grade': 'D', 'reasons': [{'reason': 'IP 查询失败', 'points': -100}],
                'error': str(data.get('error'))[:120]}
    if data.get('hosting'):
        total -= 20
        reasons.append({'reason': 'IP 为数据中心', 'points': -20})
    if data.get('proxy'):
        total -= 15
        reasons.append({'reason': 'IP 被标记为代理', 'points': -15})
    if isinstance(leak, dict) and leak.get('leak'):
        total -= 5
        reasons.append({'reason': 'IPv6 泄露', 'points': -5})
    total = max(0, total)
    if total >= 90:
        grade = 'A'
    elif total >= 75:
        grade = 'B'
    elif total >= 60:
        grade = 'C'
    else:
        grade = 'D'
    return {
        'score': total,
        'grade': grade,
        'reasons': reasons,
        'hosting': bool(data.get('hosting')),
        'flagged_proxy': bool(data.get('proxy')),
        'mobile': bool(data.get('mobile')),
        'as': str(data.get('as') or '')[:60],
        'country': str(data.get('country') or '')[:32],
    }


def check_exit(proxy: str) -> dict:
    """完整检测：IP 画像 + IPv6 泄漏 → 评分。"""
    data = ip_profile(proxy)
    leak = None
    if not data.get('error'):
        leak = ipv6_leak(proxy)
    result = score(data, leak)
    result['ip'] = str(data.get('query') or '')
    if leak:
        result['ipv6'] = {'v4': leak.get('v4', ''), 'v6': leak.get('v6', ''),
                          'leak': bool(leak.get('leak'))}
    return result
