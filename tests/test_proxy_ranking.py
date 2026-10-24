# -*- coding: utf-8 -*-
"""代理出口延迟排序测试（不发网络请求）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core import proxy_edge_probe as p


class V:
    def __init__(self, proxy, status, elapsed_ms=0):
        self.proxy = proxy
        self.status = status
        self.elapsed_ms = elapsed_ms


def _reset():
    p._VERDICTS.clear()
    p._LATENCY.clear()


def test_picks_lowest_latency_clean():
    _reset()
    p.remember(V('a', p.CLEAN, 900))
    p.remember(V('b', p.CLEAN, 200))
    p.remember(V('c', p.CLEAN, 500))
    assert p.pick_fastest_clean(['a', 'b', 'c']) == 'b'


def test_ignores_non_clean():
    _reset()
    p.remember(V('a', p.BLOCKED, 100))
    p.remember(V('b', p.CLEAN, 800))
    assert p.pick_fastest_clean(['a', 'b']) == 'b'


def test_returns_none_without_known_clean():
    _reset()
    assert p.pick_fastest_clean(['x', 'y']) is None


def test_exclude_respected():
    _reset()
    p.remember(V('a', p.CLEAN, 100))
    p.remember(V('b', p.CLEAN, 200))
    assert p.pick_fastest_clean(['a', 'b'], exclude=['a']) == 'b'


def test_clean_without_latency_sorts_last():
    _reset()
    p._VERDICTS['slow'] = (p.CLEAN, 9e18)
    p._LATENCY.pop('slow', None)
    p.remember(V('fast', p.CLEAN, 300))
    assert p.pick_fastest_clean(['slow', 'fast']) == 'fast'


def test_latency_of_unknown():
    _reset()
    assert p.latency_of('nope') is None
