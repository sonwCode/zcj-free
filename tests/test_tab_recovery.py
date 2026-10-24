# -*- coding: utf-8 -*-
"""标签断连识别与重开测试（假会话）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.tab_recovery import TabRecoveryFailed, is_page_disconnected_error, run_with_tab_recovery


class PageDisconnectedError(RuntimeError):
    pass


def test_detects_by_class_name():
    assert is_page_disconnected_error(PageDisconnectedError('boom')) is True


def test_detects_chinese_marker():
    assert is_page_disconnected_error(RuntimeError('与页面的连接已断开')) is True


def test_normal_error_not_disconnect():
    assert is_page_disconnected_error(RuntimeError('404 not found')) is False


def test_recovers_after_disconnect():
    opened = {'n': 0}
    closed = {'n': 0}

    def open_session():
        opened['n'] += 1
        return {'id': opened['n']}

    def close_session(s):
        closed['n'] += 1

    def action(session):
        if opened['n'] == 1:
            raise PageDisconnectedError('target closed')
        return 'ok-' + str(session['id'])

    out = run_with_tab_recovery(open_session, action, close_session=close_session, attempts=2)
    assert out == 'ok-2'
    assert opened['n'] == 2 and closed['n'] == 1


def test_non_disconnect_error_not_retried():
    opened = {'n': 0}

    def open_session():
        opened['n'] += 1
        return {}

    def action(session):
        raise RuntimeError('business error 400')

    try:
        run_with_tab_recovery(open_session, action, attempts=3)
        assert False, '应当抛出'
    except RuntimeError as exc:
        assert 'business' in str(exc)
    assert opened['n'] == 1, '业务错误不该重开'


def test_exhausted_raises():
    def open_session():
        return {}

    def action(session):
        raise PageDisconnectedError('closed')

    try:
        run_with_tab_recovery(open_session, action, attempts=2)
        assert False, '应当抛 TabRecoveryFailed'
    except TabRecoveryFailed:
        pass
