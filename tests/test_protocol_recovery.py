# -*- coding: utf-8 -*-
"""协议注册恢复分支的静态回归测试（防止被后续改动悄悄删掉）。"""
from pathlib import Path

SRC = Path('/opt/turb-gpt-register/core/protocol_registration.py')


def _text() -> str:
    return SRC.read_text(encoding='utf-8')


def test_recoverable_codes_declared():
    text = _text()
    for code in ('user_already_exists', 'username_already_exists',
                 'account_already_exists', 'invalid_auth_step'):
        assert code in text, code


def test_recovery_uses_existing_session():
    text = _text()
    assert 'from core.account_export import fetch_session as _fetch_session' in text
    assert '已有会话，直接恢复' in text


def test_recovery_precedes_disallowed_retry():
    text = _text()
    recovery = text.find('已有会话，直接恢复')
    except_block = text.rfind('except Exception as exc:', 0, recovery)
    disallowed = text.find('registration_disallowed" not in text', except_block)
    assert except_block != -1
    assert disallowed != -1
    assert recovery < disallowed, '恢复分支必须在 registration_disallowed 重试之前'


def test_recovery_flag_written_to_extra():
    text = _text()
    assert "info['__recovered__'] = True" in text
    assert 'create_recovered' in text
