# -*- coding: utf-8 -*-
"""出口纯净度评分的回归测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.exit_purity import score


def test_clean_residential_scores_100():
    r = score({'status': 'success', 'hosting': False, 'proxy': False, 'mobile': False,
               'as': 'AS7922 Comcast', 'country': 'United States', 'query': '1.2.3.4'})
    assert r['score'] == 100 and r['grade'] == 'A'


def test_datacenter_penalty_20():
    r = score({'hosting': True, 'proxy': False})
    assert r['score'] == 80 and r['grade'] == 'B'
    assert any(x['reason'] == 'IP 为数据中心' for x in r['reasons'])


def test_flagged_proxy_penalty_15():
    r = score({'hosting': False, 'proxy': True})
    assert r['score'] == 85 and r['grade'] == 'B'


def test_ipv6_leak_penalty_5():
    r = score({'hosting': False, 'proxy': False}, {'leak': True, 'v4': '1.1.1.1', 'v6': '2.2.2.2'})
    assert r['score'] == 95 and r['grade'] == 'A'


def test_error_scores_zero():
    r = score({'error': 'ip-api 不可达'})
    assert r['score'] == 0 and r['grade'] == 'D'


def test_combined_penalties():
    r = score({'hosting': True, 'proxy': True}, {'leak': True})
    assert r['score'] == 60 and r['grade'] == 'C'
