# -*- coding: utf-8 -*-
"""接码国家自动筛选：优先真实手机号、限制单价、按库存排序。

来源：Haohuangsix/gpt-register-pipeline 的 `core/sms_country_selector.py`。

它解决的问题正是我们的痛点：Codex 手机验证失败里 27/38 是手机号问题，
而"美国虚拟号被 OpenAI 静默拒"是一个高频原因。
hero-sms（SMS-Activate 兼容）的 `getPrices(service=dr)` 一次就能拿到
每个国家的 cost / count / physicalCount：

    {"国家id": {"dr": {"cost": 0.099, "count": 100, "physicalCount": 0}}}

  cost          单号价格（USD）
  count         可用号码总数（含虚拟）
  physicalCount 真实手机号数量（OpenAI 更可能接受）

筛选规则：
  1. cost <= max_price（默认 0.08 USD）；
  2. count > 0；
  3. 先按"是否有 physicalCount>0"分组，组内按库存降序。

只读接口，不取号、不花钱；结果缓存 5 分钟。
"""
from __future__ import annotations

import json
import logging
import time
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

CACHE_TTL = 300
_cache = {'at': 0.0, 'rows': None, 'service': ''}


class SmsSelectError(RuntimeError):
    """国家筛选失败。"""


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_prices(payload, service: str = 'dr') -> list[dict]:
    """把 getPrices 的响应解析成国家行列表。"""
    data = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            raise SmsSelectError('getPrices 返回的不是 JSON')
    if isinstance(data, dict) and 'data' in data and isinstance(data['data'], dict):
        data = data['data']
    if not isinstance(data, dict):
        raise SmsSelectError('getPrices 响应结构异常')

    rows = []
    for country_id, per_service in data.items():
        if not isinstance(per_service, dict):
            continue
        info = per_service.get(service)
        if not isinstance(info, dict):
            continue
        rows.append({
            'country': str(country_id),
            'cost': _to_decimal(info.get('cost') or info.get('price')),
            'count': _to_int(info.get('count')),
            'physical': _to_int(info.get('physicalCount') or info.get('physical_count')),
        })
    return rows


def pick_country(rows: list[dict], *, max_price: Decimal | str | float = '0.08',
                 prefer_physical: bool = True) -> dict | None:
    """按规则挑一个国家；没有满足条件的返回 None。"""
    limit = _to_decimal(max_price)
    candidates = [r for r in rows if r['count'] > 0 and r['cost'] is not None]
    if limit is not None:
        candidates = [r for r in candidates if r['cost'] <= limit]
    if not candidates:
        return None

    def sort_key(row):
        physical_rank = 0 if (prefer_physical and row['physical'] > 0) else 1
        return (physical_rank, -row['physical'], -row['count'], row['cost'])

    best = sorted(candidates, key=sort_key)[0]
    return {
        **best,
        'reason': 'physical>0' if best['physical'] > 0 else 'stock_only',
        'candidates': len(candidates),
    }


def select_country(service: str = 'dr', *, max_price='0.08', provider: str = 'herosms',
                   proxy: str | None = None, use_cache: bool = True) -> dict:
    """查询并挑选国家（带 5 分钟缓存）。"""
    if provider != 'herosms':
        raise SmsSelectError('仅 herosms 支持自动选国家（当前 provider=%s）' % provider)

    now = time.time()
    if use_cache and _cache['rows'] is not None and             _cache['service'] == service and now - _cache['at'] < CACHE_TTL:
        rows = _cache['rows']
    else:
        from core import sms_provider as sp

        http = sp._http()
        try:
            text = sp._request_herosms(http, {'action': 'getPrices', 'service': service})
        finally:
            try:
                http.close()
            except Exception:
                pass
        rows = parse_prices(text, service=service)
        _cache.update({'at': now, 'rows': rows, 'service': service})
        logger.info('[选国家] getPrices 返回 %d 个国家', len(rows))

    picked = pick_country(rows, max_price=max_price)
    if not picked:
        raise SmsSelectError('没有满足价格/库存条件的国家（max_price=%s）' % max_price)
    logger.info('[选国家] 选中 country=%s cost=%s count=%s physical=%s (%s)',
                picked['country'], picked['cost'], picked['count'],
                picked['physical'], picked['reason'])
    return picked
