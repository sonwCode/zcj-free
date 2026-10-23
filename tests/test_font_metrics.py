# -*- coding: utf-8 -*-
"""字体度量签名的离线测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.font_metrics import compare, measure, signature


def test_measure_handles_error():
    class Bad:
        def execute_script(self, *a):
            raise RuntimeError('no driver')
    out = measure(Bad())
    assert out.get('error')


def test_measure_reads_script_result():
    class Ok:
        def execute_script(self, script, samples, fonts):
            return {'Arial|0': 100.0, 'Arial|1': 50.0}
    out = measure(Ok())
    assert out['Arial|0'] == 100.0


def test_identical_signature():
    a = {'x': 1.0, 'y': 2.0}
    assert signature(a) == signature(dict(a))
    assert signature(a) != signature({'x': 1.0})


def test_compare_match():
    base = {'Arial|0': 100.0, 'Arial|1': 50.0}
    cur = {'Arial|0': 100.4, 'Arial|1': 50.0}
    r = compare(base, cur, threshold=0.02)
    assert r['verdict'] == 'match'
    assert r['drift_count'] == 0


def test_compare_drift():
    base = {'Arial|0': 100.0}
    cur = {'Arial|0': 120.0}
    r = compare(base, cur, threshold=0.02)
    assert r['verdict'] == 'drift'
    assert r['drift_count'] == 1
    assert r['worst_ratio'] == 0.2


def test_compare_missing_input():
    assert compare({}, {'a': 1})['ok'] is False
    assert compare({'a': 1}, {})['ok'] is False
