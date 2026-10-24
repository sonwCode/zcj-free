# -*- coding: utf-8 -*-
"""节点级脉冲调度器（波次 + 重排 + 节点封禁 + 探测解封）。

借鉴 asz798838958/aBaiFreeGPT 的 application/registration_pulse.py。

要解决的问题：
  OAI 对某个出口 IP 注册过多后会**临时封禁该出口**，可观测症状是
  邮箱永远收不到验证码（OTP 超时）。我们的旧做法是"整轮失败就换出口"，
  既看不住单个节点，也没有恢复机制。

这里的模型：
  * 波次（wave）：每波最多 wave_size 个并发，跨健康节点分发；
    每波有硬截止时间，一个卡住的浏览器不会拖死后续波；
  * 账号级重排：本波 no-email 的账号**重排到队尾**，而不是原地重试；
  * 节点级封禁：同一节点连续 ban_after 波 no-email → 暂停该节点；
  * 探测解封：后台按 probe_interval 对暂停节点跑一次"短超时"探测，
    成功或收到 OTP 就解封；全部暂停时脉冲循环空转，等探测结果。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: runner 返回值里用来表示"OTP 没到"的约定标记
NO_EMAIL = 'no_email'
OK = 'ok'


@dataclass
class NodeState:
    proxy: str
    status: str = 'healthy'          # healthy | paused
    consecutive_no_email: int = 0
    paused_at: float = 0.0

    def pause(self, now: float) -> None:
        self.status = 'paused'
        self.paused_at = now

    def revive(self) -> None:
        self.status = 'healthy'
        self.consecutive_no_email = 0
        self.paused_at = 0.0


@dataclass
class PulseConfig:
    wave_size: int = 3
    wave_deadline: float = 420.0     # 单波硬截止（秒）
    ban_after: int = 2               # 连续几波 no-email 就暂停节点
    probe_interval: float = 300.0    # 探测间隔（秒）
    max_waves: int = 0               # 0 = 不限
    idle_sleep: float = 5.0


@dataclass
class PulseStats:
    waves: int = 0
    submitted: int = 0
    succeeded: int = 0
    requeued: int = 0
    failed: int = 0
    timeouts: int = 0
    paused_nodes: list = field(default_factory=list)
    revived_nodes: list = field(default_factory=list)


class PulseScheduler:
    """按波次调度任务；节点封禁与解封由本类维护。

    runner(index, proxy) -> (kind, payload)
      kind == NO_EMAIL  → 视为出口被封信号，账号重排、节点计数 +1
      kind == OK        → 成功，节点计数清零
      其他              → 该账号判失败（不重排）
    """

    def __init__(self, nodes, runner, config: PulseConfig | None = None,
                 clock=time.time, sleep=time.sleep):
        self.nodes = [NodeState(str(p)) for p in (nodes or []) if str(p)]
        if not self.nodes:
            raise ValueError('至少需要一个节点')
        self.runner = runner
        self.cfg = config or PulseConfig()
        self._clock = clock
        self._sleep = sleep
        self.queue: list = []
        self.stats = PulseStats()
        self._next_node = 0
        self._lock = threading.Lock()

    # ---- 节点选择 ----
    def healthy_nodes(self) -> list:
        return [n for n in self.nodes if n.status == 'healthy']

    def _pick_node(self):
        healthy = self.healthy_nodes()
        if not healthy:
            return None
        node = healthy[self._next_node % len(healthy)]
        self._next_node += 1
        return node

    # ---- 结果处理 ----
    def _apply(self, node: NodeState, kind: str) -> None:
        if kind == OK:
            node.consecutive_no_email = 0
            self.stats.succeeded += 1
        elif kind == NO_EMAIL:
            node.consecutive_no_email += 1
            self.stats.requeued += 1
            if node.consecutive_no_email >= max(1, int(self.cfg.ban_after)):
                node.pause(self._clock())
                self.stats.paused_nodes.append(node.proxy)
                logger.warning('[Pulse] 节点连续 %d 波 no-email，暂停：%s',
                               node.consecutive_no_email, '***')
        else:
            self.stats.failed += 1

    # ---- 单波 ----
    def run_wave(self) -> bool:
        """跑一波；返回是否还有待处理任务。"""
        if not self.queue:
            return False
        healthy = self.healthy_nodes()
        if not healthy:
            self._probe_paused()
            self._sleep(self.cfg.idle_sleep)
            return bool(self.queue)
        batch = []
        while self.queue and len(batch) < max(1, int(self.cfg.wave_size)):
            node = self._pick_node()
            if node is None:
                break
            batch.append((self.queue.pop(0), node))
        if not batch:
            return bool(self.queue)
        self.stats.waves += 1
        self.stats.submitted += len(batch)
        deadline = self._clock() + max(1.0, float(self.cfg.wave_deadline))
        results = {}
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {pool.submit(self.runner, idx, node.proxy): (idx, node)
                       for idx, node in batch}
            done, not_done = wait(futures, timeout=max(0.0, deadline - self._clock()),
                                  return_when=FIRST_COMPLETED)
            observed = set(done)
            while not_done and self._clock() < deadline:
                more_done, not_done = wait(
                    not_done, timeout=max(0.0, deadline - self._clock()),
                    return_when=FIRST_COMPLETED)
                if not more_done:
                    break
                observed |= set(more_done)
            for future in observed:
                idx, node = futures[future]
                try:
                    kind, _payload = future.result(timeout=0)
                except Exception as exc:
                    kind = 'error:%s' % type(exc).__name__
                results[idx] = (node, kind)
            for future in not_done:
                idx, node = futures[future]
                future.cancel()
                results[idx] = (node, 'timeout')
                self.stats.timeouts += 1
        for idx, (node, kind) in results.items():
            if kind == 'timeout':
                node.consecutive_no_email += 1
                self.queue.append(idx)
                continue
            self._apply(node, kind)
            if kind == NO_EMAIL:
                self.queue.append(idx)
        return bool(self.queue)

    # ---- 探测解封 ----
    def _probe_paused(self) -> None:
        now = self._clock()
        for node in self.nodes:
            if node.status != 'paused':
                continue
            if now - node.paused_at < max(1.0, float(self.cfg.probe_interval)):
                continue
            try:
                kind, _payload = self.runner(-1, node.proxy)
            except Exception:
                kind = 'error'
            # 只有"注册成功"或"明确收到 OTP"才算恢复；
            # 探测本身返回 no-email 恰恰说明还没恢复（这点极易写反）。
            if kind in (OK, 'otp_received'):
                # 收到 OTP 或注册成功，说明出口已恢复
                node.revive()
                self.stats.revived_nodes.append(node.proxy)
                logger.info('[Pulse] 节点探测通过，解封')
            else:
                node.paused_at = now

    # ---- 主循环 ----
    def run(self, indices) -> PulseStats:
        self.queue = list(indices or [])
        waves = 0
        while self.queue:
            alive = self.run_wave()
            waves += 1
            if self.cfg.max_waves and waves >= int(self.cfg.max_waves):
                break
            if not alive:
                break
        return self.stats
