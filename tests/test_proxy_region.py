# -*- coding: utf-8 -*-
"""代理地区过滤测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')
from core.proxy_region import filter_by_region, pick_by_region, region_of, regions_available

POOL = [
    "http://u:p@us.1024proxy.io:3000/?region-US",
    "http://u:p@vn.proxy.io:3000/?region-VN",
    "http://u:p@vn2.proxy.io:3000/?region-VN",
    "http://plain.proxy.io:8080",
]


def test_region_of():
    assert region_of(POOL[0]) == "US"
    assert region_of(POOL[1]) == "VN"
    assert region_of(POOL[3]) == ""


def test_filter_by_region():
    assert len(filter_by_region(POOL, "VN")) == 2
    assert len(filter_by_region(POOL, "US")) == 1
    assert len(filter_by_region(POOL, "")) == 4


def test_pick_by_region():
    got = pick_by_region(POOL, "VN")
    assert got == POOL[1]
    assert pick_by_region(POOL, "JP") is None


def test_pick_exclude():
    got = pick_by_region(POOL, "VN", exclude=[POOL[1]])
    assert got == POOL[2]


def test_regions_available():
    stat = regions_available(POOL)
    assert stat.get("VN") == 2 and stat.get("US") == 1 and stat.get("未标注") == 1