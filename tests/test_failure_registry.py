# -*- coding: utf-8 -*-
"""失败分类注册表的回归测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.failure_registry import (classify_error, is_terminal_registration_error,
                                   rotate_egress_suggested, summarize)


def test_network_class():
    assert classify_error('SSLError: SSL_ERROR_SYSCALL') == 'network'
    assert classify_error({'error': 'curl: (35) connection reset'}) == 'network'


def test_edge_class():
    assert classify_error('CloudflareChallengeError: Cloudflare challenge') == 'edge_challenge'


def test_mailbox_not_egress():
    text = 'RemailError: 等待 Remail 验证码超时；收件箱为空'
    assert classify_error(text) == 'mailbox'
    assert rotate_egress_suggested(text) is False, '邮箱侧失败换出口无意义'


def test_account_class_dropped():
    text = 'create_account 失败 status=400 {"code":"account_deactivated"}'
    assert classify_error(text) == 'account'


def test_rate_limit_rotate():
    text = 'create_account 失败 status=429 too many requests'
    assert classify_error(text) == 'rate_limit'
    assert rotate_egress_suggested(text) is True


def test_terminal():
    assert is_terminal_registration_error('registration_cancelled') is True
    assert is_terminal_registration_error('SSLError') is False


def test_unknown():
    assert classify_error('something weird') == 'unknown'


def test_summarize():
    counts = summarize([{'success': True}, {'error': 'SSLError'}, {'error': '收件箱为空'}])
    assert counts.get('success') == 1
    assert counts.get('network') == 1
    assert counts.get('mailbox') == 1
