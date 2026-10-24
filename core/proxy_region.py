# -*- coding: utf-8 -*-
"""按目标国家过滤代理出口。

背景：竞品后台显示注册国家为 VN（越南），而我们池里 10 条代理全是 US。
注册出口国家会影响账号地区画像与试用资格，所以需要"按国家挑出口"的能力；
有了 VN 代理后，把 REGISTRATION_TARGET_COUNTRY 设为 VN 即可全程使用 VN 出口。
"""
from __future__ import annotations

import re

_REGION_RE = re.compile(r"region-([A-Za-z]{2})", re.IGNORECASE)


def region_of(proxy: str) -> str:
    """从代理 URL 里解析地区码（如 region-US → US）；解析不到返回空串。"""
    m = _REGION_RE.search(str(proxy or ""))
    return m.group(1).upper() if m else ""


def filter_by_region(pool, region: str) -> list:
    """返回该地区的出口；region 为空则原样返回。"""
    want = str(region or "").strip().upper()
    items = [str(p) for p in (pool or []) if str(p or "").strip()]
    if not want:
        return items
    return [p for p in items if region_of(p) == want]


def pick_by_region(pool, region: str, exclude=None) -> str | None:
    """从指定地区里挑一条（跳过 exclude），没有则返回 None。"""
    excluded = set(exclude or [])
    for p in filter_by_region(pool, region):
        if p in excluded:
            continue
        return p
    return None


def regions_available(pool) -> dict:
    """统计池内各地区条数，便于判断是否需要补某国代理。"""
    out = {}
    for p in pool or []:
        key = region_of(p) or "未标注"
        out[key] = out.get(key, 0) + 1
    return out