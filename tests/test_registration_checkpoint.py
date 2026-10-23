# -*- coding: utf-8 -*-
"""注册断点模块的回归测试。"""
import sys
import tempfile
sys.path.insert(0, '/opt/turb-gpt-register')

from core.registration_checkpoint import (clear, load, resumable, save, update)


def test_save_and_load():
    with tempfile.TemporaryDirectory() as tmp:
        email = 'unit-test@example.com'
        save(email, {'account_created': True, 'access_token': 'tok'}, base_dir=tmp)
        data = load(email, base_dir=tmp)
        assert data.get('account_created') is True
        assert data.get('access_token') == 'tok'
        assert 'updated_at' in data


def test_update_merges():
    with tempfile.TemporaryDirectory() as tmp:
        email = 'unit-merge@example.com'
        update(email, otp_validated=True)
        update(email, account_created=True)
        data = load(email, base_dir=tmp)
        assert data.get('otp_validated') is True
        assert data.get('account_created') is True


def test_resumable_requires_token_and_created():
    with tempfile.TemporaryDirectory() as tmp:
        email = 'unit-resume@example.com'
        save(email, {'account_created': True}, base_dir=tmp)
        assert resumable(email, base_dir=tmp)['resumable'] is False
        save(email, {'account_created': True, 'access_token': 'tok'}, base_dir=tmp)
        info = resumable(email, base_dir=tmp)
        assert info['resumable'] is True
        assert info['access_token'] == 'tok'


def test_clear_removes_file():
    with tempfile.TemporaryDirectory() as tmp:
        email = 'unit-clear@example.com'
        save(email, {'account_created': True}, base_dir=tmp)
        assert clear(email, base_dir=tmp) is True
        assert load(email, base_dir=tmp) == {}
