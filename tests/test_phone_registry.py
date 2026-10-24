# -*- coding: utf-8 -*-
"""已用号码登记表的离线测试。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, '/opt/turb-gpt-register')

from core.phone_registry import (filter_unused, is_used, mark_used,
                                 normalize, stats)


def test_normalize_variants():
    assert normalize('+1 (555) 123-4567') == '15551234567'
    assert normalize('8613800138000') == '8613800138000'
    assert normalize('') == ''


def test_mark_and_check():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'used.json'
        assert is_used('+15551234567', path=path) is False
        assert mark_used('+15551234567', 'a@b.c', path=path) is True
        assert is_used('15551234567', path=path) is True, '归一后应命中'


def test_mark_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'used.json'
        mark_used('15550001111', path=path)
        assert mark_used('15550001111', path=path) is False
        assert stats(path=path)['used'] == 1


def test_filter_unused():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'used.json'
        mark_used('15551110000', path=path)
        left = filter_unused(['+1 555 111 0000', '+1 555 222 0000'], path=path)
        assert left == ['+1 555 222 0000']


def test_empty_number_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'used.json'
        assert mark_used('', path=path) is False
        assert is_used('', path=path) is False
