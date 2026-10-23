# -*- coding: utf-8 -*-
"""告警模块的离线测试。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, '/opt/turb-gpt-register')

from core.alerting import (build_generic, build_telegram, build_wecom,
                           dead_ratio_exceeded, send, should_send)


def test_wecom_payload_shape():
    p = build_wecom('测试')
    assert p['msgtype'] == 'text' and p['text']['content'] == '测试'


def test_telegram_payload_shape():
    p = build_telegram('hi', '123')
    assert p['chat_id'] == '123' and p['text'] == 'hi'


def test_should_send_throttles():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'state.json'
        assert should_send('k', 1800, path=path, now=1000.0) is True
        assert should_send('k', 1800, path=path, now=1500.0) is False
        assert should_send('k', 1800, path=path, now=3000.0) is True


def test_should_send_separate_keys():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'state.json'
        assert should_send('a', 1800, path=path, now=1000.0) is True
        assert should_send('b', 1800, path=path, now=1000.0) is True


def test_send_without_webhook():
    assert send('x', '')['ok'] is False


def test_send_uses_sender():
    calls = []
    send('hello', 'http://example/hook', 'wecom', sender=lambda u, p: calls.append((u, p)))
    assert calls and calls[0][0] == 'http://example/hook'
    assert calls[0][1]['msgtype'] == 'text'


def test_dead_ratio_threshold():
    assert dead_ratio_exceeded(10, 4, 0.3) is True
    assert dead_ratio_exceeded(10, 2, 0.3) is False
    assert dead_ratio_exceeded(3, 3, 0.3) is False, '样本小于 5 不告警'
