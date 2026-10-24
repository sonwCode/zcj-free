# -*- coding: utf-8 -*-
"""号码锁的离线测试：同号串行、不同号并行。"""
import sys
import threading
import time
sys.path.insert(0, '/opt/turb-gpt-register')

from core.phone_lock import phone_lock


def test_same_number_serializes():
    order = []
    started = threading.Event()

    def worker(tag, hold):
        with phone_lock('+15550001111'):
            order.append(('start', tag))
            if tag == 'A':
                started.set()
            time.sleep(hold)
            order.append(('end', tag))

    a = threading.Thread(target=worker, args=('A', 0.25))
    b = threading.Thread(target=worker, args=('B', 0.05))
    a.start()
    started.wait(2)
    b.start()
    a.join(5)
    b.join(5)
    tags = [t for kind, t in order]
    # B 不能在 A 结束前开始
    assert tags.index('A') < tags.index('B')
    assert order.index(('end', 'A')) < order.index(('start', 'B'))


def test_different_numbers_do_not_block():
    results = []

    def worker(tag):
        with phone_lock('+1555000' + tag):
            results.append(tag)
            time.sleep(0.05)

    threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(4)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    elapsed = time.time() - start
    assert len(results) == 4
    assert elapsed < 0.5, '不同号码不该互相阻塞'
