# -*- coding: utf-8 -*-
"""OTP 拒绝记录的离线测试（用临时文件，不碰生产数据）。"""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, '/opt/turb-gpt-register')

from core import otp_rejection_store as s


def _tmp() -> Path:
    return Path(tempfile.mkdtemp()) / 'otp.json'


def test_note_then_is_rejected():
    p = _tmp()
    assert s.is_rejected('a@x', '123456', path=p) is False
    assert s.note_rejection('a@x', '123456', path=p) is True
    assert s.is_rejected('a@x', '123456', path=p) is True


def test_other_code_not_rejected():
    p = _tmp()
    s.note_rejection('a@x', '111111', path=p)
    assert s.is_rejected('a@x', '222222', path=p) is False


def test_other_email_isolated():
    p = _tmp()
    s.note_rejection('a@x', '111111', path=p)
    assert s.is_rejected('b@x', '111111', path=p) is False


def test_filter_candidates():
    p = _tmp()
    s.note_rejection('a@x', '111111', path=p)
    out = s.filter_candidates('a@x', ['111111', '222222'], path=p)
    assert out == ['222222'], out


def test_ttl_expiry():
    p = _tmp()
    now = time.time()
    s.note_rejection('a@x', '111111', path=p, now=now)
    assert s.is_rejected('a@x', '111111', path=p, now=now) is True
    later = now + s.TTL_SECONDS + 10
    assert s.is_rejected('a@x', '111111', path=p, now=later) is False


def test_no_plaintext_on_disk():
    p = _tmp()
    s.note_rejection('a@x', '123456', path=p)
    raw = p.read_text(encoding='utf-8')
    assert '123456' not in raw, '磁盘上不应出现明文验证码'
    assert 'a@x' in raw
