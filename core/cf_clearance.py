# -*- coding: utf-8 -*-
"""Cloudflare 挑战等待与清除。

借鉴 AuuCoder/gptGrok2api 的 captcha-solver/cloudflare/solve.py：
它的做法不是"见到挑战就失败"，而是**打开真实 URL 后轮询**，直到
`cf_clearance` cookie 出现、挑战标记消失；Managed Challenge 会尝试点一次
Turnstile 复选框，JS Challenge 则自动通过。

我们此前的行为：`_raise_if_cloudflare_challenge()` 一看到标记就抛异常，
于是把"浏览器再等 5 秒就自己过了"错判成"这条出口坏了"，白白换出口重跑。

本模块只做两件事：
  1. `wait_for_clearance()`：轮询页面状态，给真实浏览器自愈的时间窗；
  2. `click_turnstile_if_present()`：best-effort 点一次 Turnstile 复选框。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    'just a moment',
    'performing security verification',
    'checking your browser',
    'verify you are human',
    'attention required',
    'cf-chl',
)


def looks_like_challenge(state: dict) -> bool:
    """按页面状态判断是否仍停留在 CF 挑战页。"""
    if not isinstance(state, dict):
        return False
    text = ' '.join([
        str(state.get('url') or ''),
        str(state.get('title') or ''),
        str(state.get('text') or ''),
        ' '.join(str(x or '') for x in (state.get('errors') or [])),
    ]).lower()
    return any(marker in text for marker in CHALLENGE_MARKERS)


_TURNSTILE_CLICK_JS = '''
(function () {
  try {
    var frames = Array.prototype.slice.call(document.querySelectorAll('iframe'));
    var target = frames.find(function (f) {
      return (f.src || '').indexOf('challenges.cloudflare.com') >= 0;
    });
    if (!target) { return 'no-iframe'; }
    var r = target.getBoundingClientRect();
    if (!r || r.width < 10 || r.height < 10) { return 'not-visible'; }
    window.__cf_click_box = { x: r.left + 30, y: r.top + r.height / 2 };
    return 'ready';
  } catch (e) { return 'error'; }
})()
'''


def click_turnstile_if_present(driver) -> str:
    """best-effort：若页面内有 Turnstile iframe，点一次它的复选框位置。"""
    try:
        status = driver.execute_script(_TURNSTILE_CLICK_JS)
    except Exception as exc:
        return 'script-failed:%s' % type(exc).__name__
    if status != 'ready':
        return str(status)
    try:
        box = driver.execute_script('window.__cf_click_box || null')
        if not box:
            return 'no-box'
        driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': float(box['x']), 'y': float(box['y']),
            'button': 'left', 'clickCount': 1,
        })
        driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': float(box['x']), 'y': float(box['y']),
            'button': 'left', 'clickCount': 1,
        })
        return 'clicked'
    except Exception as exc:
        return 'click-failed:%s' % type(exc).__name__


def wait_for_clearance(driver, state_getter, *, deadline_s: float = 45.0,
                       poll_s: float = 1.5, try_click: bool = True) -> dict:
    """轮询直到挑战消失或超时；返回最后一次页面状态（不抛异常）。"""
    started = time.time()
    onclick_sent = False
    last_state: dict = {}
    while True:
        try:
            last_state = state_getter(driver) or {}
        except Exception as exc:
            logger.debug('[CF] 读取页面状态失败：%s', str(exc)[:100])
            last_state = {}
        if not looks_like_challenge(last_state):
            elapsed = time.time() - started
            if elapsed > 0.5:
                logger.info('[CF] 挑战已通过（等待 %.1fs）', elapsed)
            return last_state
        if try_click and not onclick_sent:
            onclick_sent = True
            result = click_turnstile_if_present(driver)
            logger.info('[CF] 尝试点击 Turnstile：%s', result)
        if time.time() - started >= max(1.0, float(deadline_s)):
            logger.warning('[CF] 等待 %.0fs 仍未通过挑战', deadline_s)
            return last_state
        time.sleep(max(0.3, float(poll_s)))
