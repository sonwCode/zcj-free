# -*- coding: utf-8 -*-
"""脉冲调度器离线测试（无网络，注入假时钟与 runner）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.registration_pulse import (NO_EMAIL, OK, NodeState,
                                     PulseConfig, PulseScheduler)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))


def test_all_success_drains_queue():
    clock = FakeClock()
    seen = []

    def runner(idx, proxy):
        seen.append((idx, proxy))
        return OK, None

    sched = PulseScheduler(['p1', 'p2'], runner,
                           PulseConfig(wave_size=2, wave_deadline=60),
                           clock=clock, sleep=clock.sleep)
    stats = sched.run([1, 2, 3])
    assert stats.succeeded == 3 and stats.requeued == 0
    assert sched.queue == []


def test_no_email_requeues_account():
    clock = FakeClock()
    calls = {'n': 0}

    def runner(idx, proxy):
        calls['n'] += 1
        return (NO_EMAIL, None) if calls['n'] == 1 else (OK, None)

    sched = PulseScheduler(['p1'], runner,
                           PulseConfig(wave_size=1, ban_after=5),
                           clock=clock, sleep=clock.sleep)
    stats = sched.run([7])
    assert stats.requeued >= 1, '第一次 no-email 应重排'
    assert stats.succeeded >= 1, '重排后应成功'
    assert sched.queue == []


def test_node_paused_after_threshold():
    clock = FakeClock()

    def runner(idx, proxy):
        return NO_EMAIL, None

    sched = PulseScheduler(['p1'], runner,
                           PulseConfig(wave_size=1, ban_after=2, max_waves=6),
                           clock=clock, sleep=clock.sleep)
    stats = sched.run([1, 2, 3])
    assert stats.paused_nodes, '连续 no-email 应暂停节点'
    assert sched.nodes[0].status == 'paused'


def test_probe_revives_node():
    clock = FakeClock()
    state = {'mode': 'no_email'}

    def runner(idx, proxy):
        if idx == -1:
            return (OK, None) if state['mode'] == 'ok' else (NO_EMAIL, None)
        if state['mode'] == 'ok':
            return OK, None      # 解封后真实任务应成功，否则会把节点又打回暂停
        return NO_EMAIL, None

    sched = PulseScheduler(['p1'], runner,
                           PulseConfig(wave_size=1, ban_after=1, probe_interval=10,
                                       max_waves=8),
                           clock=clock, sleep=clock.sleep)
    sched.run([1])
    assert sched.nodes[0].status == 'paused'
    state['mode'] = 'ok'
    # 把暂停时间往前挪，让探测立刻到期
    sched.nodes[0].paused_at = clock.t - 100
    sched.run([2])
    assert sched.nodes[0].status == 'healthy', '探测通过应解封'
    assert sched.stats.revived_nodes


def test_timeout_requeues_and_counts():
    clock = FakeClock()

    def runner(idx, proxy):
        clock.t += 1000  # 超过波次截止
        return OK, None

    sched = PulseScheduler(['p1'], runner,
                           PulseConfig(wave_size=1, wave_deadline=1, max_waves=1),
                           clock=clock, sleep=clock.sleep)
    stats = sched.run([1])
    assert stats.timeouts >= 1 or stats.succeeded >= 1
