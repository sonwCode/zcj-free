# -*- coding: utf-8 -*-
"""批次波次判定回归测试（复现 2951461586 的批次 25288 结论）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.batch_pulse import detect_exit_ban, is_dispatch_side_failure, wave_summary


def test_mailbox_failure_is_not_dispatch_side():
    assert is_dispatch_side_failure({'error': 'otp timeout, 收件箱为空'}) is False
    assert is_dispatch_side_failure({'error': '验证码超时'}) is False


def test_dispatch_failure_detected():
    assert is_dispatch_side_failure({'error': 'email_otp_send_stuck'}) is True
    assert is_dispatch_side_failure({'error': 'HTTP 429 too many requests'}) is True
    assert is_dispatch_side_failure({'error': 'Cloudflare challenge on OTP page'}) is True


def test_mixed_wave_is_not_ban():
    # 批次 25288 wave 2：3 超时 + 1 拿到码 → 出口是通的
    wave = [
        {'error': 'email_otp_send_stuck'},
        {'error': 'email_otp_send_stuck'},
        {'error': 'email_otp_send_stuck'},
        {'success': True},
    ]
    assert detect_exit_ban(wave) is False


def test_all_dispatch_failure_is_ban():
    wave = [{'error': 'email_otp_send_stuck'} for _ in range(4)]
    assert detect_exit_ban(wave) is True


def test_below_threshold_not_ban():
    wave = [{'error': 'email_otp_send_stuck'}, {'success': True}]
    assert detect_exit_ban(wave, threshold=2) is False


def test_mailbox_side_wave_not_ban():
    wave = [{'error': 'otp timeout'}, {'error': 'otp timeout'}]
    assert detect_exit_ban(wave) is False


def test_summary_shape():
    wave = [{'error': 'email_otp_send_stuck'}, {'success': True}]
    summary = wave_summary(wave)
    assert summary['total'] == 2
    assert summary['success'] == 1
    assert summary['dispatch_side'] == 1
    assert summary['exit_ban_evidence'] is False
