# -*- coding: utf-8 -*-
"""按邮箱 OTP 锁测试。"""
import sys, threading, time
sys.path.insert(0, '/opt/turb-gpt-register')

from core.otp_lock import OtpLockBusy, active_count, otp_lock


def test_same_email_serializes():
    order = []
    started = threading.Event()

    def worker(tag, hold):
        with otp_lock('a@x'):
            order.append(('start', tag))
            if tag == 'A':
                started.set()
            time.sleep(hold)
            order.append(('end', tag))

    a = threading.Thread(target=worker, args=('A', 0.2))
    b = threading.Thread(target=worker, args=('B', 0.02))
    a.start(); started.wait(2); b.start()
    a.join(3); b.join(3)
    assert order.index(('end', 'A')) < order.index(('start', 'B')), order


def test_different_emails_not_blocked():
    out = []

    def worker(tag):
        with otp_lock(tag + '@x'):
            out.append(tag)
            time.sleep(0.05)

    ts = [threading.Thread(target=worker, args=(str(i),)) for i in range(4)]
    start = time.time()
    for t in ts: t.start()
    for t in ts: t.join(3)
    assert len(out) == 4
    assert time.time() - start < 0.5


def test_non_blocking_raises_when_busy():
    started = threading.Event()
    release = threading.Event()

    def holder():
        with otp_lock('busy@x'):
            started.set()
            release.wait(3)

    t = threading.Thread(target=holder)
    t.start(); started.wait(2)
    try:
        try:
            with otp_lock('busy@x', blocking=False):
                assert False, '应当抛 OtpLockBusy'
        except OtpLockBusy:
            pass
    finally:
        release.set(); t.join(3)


def test_email_is_case_insensitive():
    started = threading.Event()
    release = threading.Event()

    def holder():
        with otp_lock('Case@X'):
            started.set()
            release.wait(3)

    t = threading.Thread(target=holder)
    t.start(); started.wait(2)
    try:
        try:
            with otp_lock('case@x', blocking=False):
                assert False, '大小写不同应视为同一邮箱'
        except OtpLockBusy:
            pass
    finally:
        release.set(); t.join(3)


def test_empty_email_passthrough():
    with otp_lock(''):
        pass
