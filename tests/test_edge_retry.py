# -*- coding: utf-8 -*-
"""边缘挑战重试的回归测试。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.edge_retry import is_edge_challenge, run_with_edge_retry


class CFErr(RuntimeError):
    error_code = 'cloudflare_challenge'


def test_detect_by_code():
    assert is_edge_challenge(CFErr('x')) is True


def test_detect_by_message():
    assert is_edge_challenge(RuntimeError('Cloudflare challenge on OTP page')) is True
    assert is_edge_challenge(RuntimeError('cf-chl-bypass')) is True
    assert is_edge_challenge(RuntimeError('Just a moment...')) is True


def test_normal_error_not_edge():
    assert is_edge_challenge(RuntimeError('email_submit_stalled')) is False
    assert is_edge_challenge(ValueError('boom')) is False


def test_retry_switches_exit():
    calls = []

    def fake_call(**kw):
        calls.append(kw.get('proxy'))
        if len(calls) == 1:
            raise CFErr('Cloudflare challenge')
        return {'success': True}

    result = run_with_edge_retry(fake_call, {'email': 'x@y.z'}, max_attempts=2)
    assert result.get('success') is True
    assert len(calls) == 2
    assert calls[0] != calls[1], '重试必须换出口'


def test_normal_error_not_retried():
    calls = []

    def fake_fail(**kw):
        calls.append(1)
        raise RuntimeError('boom')

    try:
        run_with_edge_retry(fake_fail, {}, max_attempts=2)
    except RuntimeError:
        pass
    assert len(calls) == 1
