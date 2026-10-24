# -*- coding: utf-8 -*-
"""JWT 过期判断与巡检锁的离线测试。"""
import base64, json, sys, time
sys.path.insert(0, '/opt/turb-gpt-register')

from core.token_expiry import decode_jwt_payload_unverified, seconds_until_expiry, token_expiry, token_needs_refresh
from core.inspection_lease import InspectionLeaseBusy, acquire_inspection_lease


def _jwt(payload):
    def b64(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip('=')
    return b64({'alg': 'none'}) + '.' + b64(payload) + '.' + 'sig'


def test_expired_token_needs_refresh():
    assert token_needs_refresh(_jwt({'exp': int(time.time()) - 60})) is True


def test_valid_token_does_not_need_refresh():
    assert token_needs_refresh(_jwt({'exp': int(time.time()) + 3600})) is False


def test_garbage_token_needs_refresh():
    assert token_needs_refresh('not-a-jwt') is True
    assert token_needs_refresh('') is True


def test_no_exp_needs_refresh():
    assert token_needs_refresh(_jwt({'sub': 'x'})) is True


def test_bearer_prefix_stripped():
    tok = 'Bearer ' + _jwt({'exp': int(time.time()) + 600})
    assert token_needs_refresh(tok) is False
    assert token_expiry(tok) is not None
    assert seconds_until_expiry(tok) > 0


def test_decode_returns_dict():
    payload = decode_jwt_payload_unverified(_jwt({'exp': 1, 'email': 'a@b'}))
    assert payload.get('email') == 'a@b'


def test_lease_is_exclusive():
    with acquire_inspection_lease('unit-test'):
        try:
            with acquire_inspection_lease('unit-test'):
                assert False, 'should not acquire twice'
        except InspectionLeaseBusy:
            pass
    with acquire_inspection_lease('unit-test'):
        pass
