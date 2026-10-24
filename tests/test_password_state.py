# -*- coding: utf-8 -*-
"""密码状态与分类测试。"""
import json, sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.password_state import PasswordRequired, has_password, list_missing, password_state, require_password, stored_password


def _acc(**kw):
    return kw


def test_detects_direct_field():
    assert has_password(_acc(registration_password='p1')) is True


def test_detects_nested_extra_json():
    acc = _acc(extra_json=json.dumps({'registration_password': 'p2'}))
    assert stored_password(acc) == 'p2'
    assert password_state(acc) == 'set'


def test_detects_extra_json_as_dict():
    assert has_password(_acc(extra_json={'registration_password': 'p3'})) is True


def test_missing():
    assert has_password(_acc(extra_json='{}')) is False
    assert password_state(_acc()) == 'missing'


def test_require_raises():
    try:
        require_password(_acc(email='a@x', id=7))
        assert False, '应当抛 PasswordRequired'
    except PasswordRequired as exc:
        assert exc.email == 'a@x' and exc.account_id == 7


def test_list_missing():
    rows = [_acc(id=1, registration_password='x'), _acc(id=2)]
    out = list_missing(rows)
    assert len(out) == 1 and out[0]['id'] == 2


def test_failure_class_registered():
    from core.failure_registry import classify_error, class_of

    cls = class_of('password_required')
    assert cls is not None, 'password_required 未注册'
    assert cls.retain_for_future_batch is True
    assert classify_error('password_required: 账号需要密码但未保存') == 'password_required'
