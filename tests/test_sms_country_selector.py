# -*- coding: utf-8 -*-
"""接码国家选择器测试（纯解析/排序，不发网络）。"""
import sys
from decimal import Decimal
sys.path.insert(0, '/opt/turb-gpt-register')

from core.sms_country_selector import SmsSelectError, parse_prices, pick_country

SAMPLE = {
    "0": {"dr": {"cost": 0.05, "count": 500, "physicalCount": 0}},     # 便宜但纯虚拟
    "187": {"dr": {"cost": 0.07, "count": 40, "physicalCount": 30}},   # 有真实号
    "6": {"dr": {"cost": 0.20, "count": 900, "physicalCount": 900}},   # 超价
    "22": {"dr": {"count": 0, "physicalCount": 0}},                    # 无货
    "49": {"xx": {"cost": 0.01, "count": 10}},                         # 服务不匹配
}


def test_parse_filters_service():
    rows = parse_prices(SAMPLE, service='dr')
    ids = sorted(r['country'] for r in rows)
    assert ids == ['0', '187', '22', '6'], ids


def test_prefers_physical_numbers():
    rows = parse_prices(SAMPLE, service='dr')
    best = pick_country(rows, max_price='0.08')
    assert best is not None
    # 纯虚拟的 0 更便宜，但有真实号的 187 应当优先
    assert best['country'] == '187', best
    assert best['reason'] == 'physical>0'


def test_price_limit_excludes():
    rows = parse_prices(SAMPLE, service='dr')
    best = pick_country(rows, max_price='0.06')
    assert best['country'] == '0', best
    assert best['reason'] == 'stock_only'


def test_no_candidates():
    rows = parse_prices(SAMPLE, service='dr')
    assert pick_country(rows, max_price='0.001') is None


def test_bad_payload_raises():
    try:
        parse_prices('not json', service='dr')
        assert False, '应当抛错'
    except SmsSelectError:
        pass


def test_missing_cost_skipped():
    rows = parse_prices({"9": {"dr": {"count": 10}}}, service='dr')
    best = pick_country(rows, max_price='1')
    assert best is None, best
