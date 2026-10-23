# -*- coding: utf-8 -*-
"""CF 挑战等待逻辑的回归测试（用假 driver 验证）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.cf_clearance import looks_like_challenge, wait_for_clearance


class FakeDriver:
    def __init__(self, states):
        self.states = list(states)
        self.calls = 0

    def execute_script(self, script):
        return 'no-iframe'

    def execute_cdp_cmd(self, cmd, params):
        return None


def _getter_factory(driver):
    def getter(_driver):
        idx = min(driver.calls, len(driver.states) - 1)
        driver.calls += 1
        return driver.states[idx]

    return getter


def test_marker_detection():
    assert looks_like_challenge({'title': 'Just a moment...'}) is True
    assert looks_like_challenge({'text': 'Checking your browser before accessing'}) is True
    assert looks_like_challenge({'url': 'https://auth.openai.com/email-verification'}) is False


def test_returns_immediately_when_clean():
    driver = FakeDriver([{'title': 'Verify your email'}])
    state = wait_for_clearance(driver, _getter_factory(driver), deadline_s=5, poll_s=0.1)
    assert state.get('title') == 'Verify your email'
    assert driver.calls == 1


def test_waits_until_cleared():
    driver = FakeDriver([
        {'title': 'Just a moment...'},
        {'title': 'Just a moment...'},
        {'title': 'Verify your email'},
    ])
    state = wait_for_clearance(driver, _getter_factory(driver), deadline_s=5, poll_s=0.1)
    assert state.get('title') == 'Verify your email'
    assert driver.calls >= 3


def test_gives_up_after_deadline():
    driver = FakeDriver([{'title': 'Just a moment...'}])
    state = wait_for_clearance(driver, _getter_factory(driver), deadline_s=1, poll_s=0.2)
    assert looks_like_challenge(state) is True
