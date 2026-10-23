# -*- coding: utf-8 -*-
"""批次波次判定：区分"出口被封"与"账号级偶发失败"。

借鉴 2951461586/GPT-Register-Tool 的 registration/pulse.py。

它用实测批次 25288 说明了一件事：
  旧判据只看"失败数 ≥ 阈值"，把 N 个互不相关的账号级停顿读成了出口被封，
  白白停 60s×2；而后续波次同一出口、同样配置却 0 超时。

正确的判据是**整轮一致**：
  每个账号在池里各自钉一条出口，所以只要一轮里有**任何一个账号成功**，
  就同时证明池子、目标域名、发码链路都是通的 —— 剩余的失败是账号级的。

另外必须把两类失败分开：
  派发侧（服务端没把 OTP 发出去）→ 可能是出口被封
  邮箱侧（码没到邮箱）→ 换出口毫无意义
"""
from __future__ import annotations

from typing import Any

#: 派发侧失败标记：服务端没有完成 OTP 派发（出口级信号）
DISPATCH_MARKERS = (
    'email_otp_send_stuck',
    'otp_dispatch_blocked',
    'otp_dispatch_failed',
    'rate_limited',
    'too many requests',
    'http-429',
    'cloudflare challenge',
    'cf-chl',
)

#: 邮箱侧失败标记：码没到邮箱（与出口无关）
MAILBOX_MARKERS = (
    'otp timeout',
    'no code',
    '收件箱为空',
    '验证码超时',
    'mailbox',
)


def _text_of(result: Any) -> str:
    if isinstance(result, dict):
        parts = [str(result.get(k) or '') for k in ('error', 'status', 'stage', 'reason', 'message')]
        return ' '.join(parts).lower()
    return str(result or '').lower()


def is_dispatch_side_failure(result: Any) -> bool:
    """该结果是否是"服务端没派发 OTP"这一侧的证据（排除邮箱侧）。"""
    text = _text_of(result)
    if any(m in text for m in MAILBOX_MARKERS):
        return False
    return any(m in text for m in DISPATCH_MARKERS)


def detect_exit_ban(wave_results: list, threshold: int = 2) -> bool:
    """判断一轮结果是否构成"出口被封"的证据。

    条件（两者都要满足）：
      1. 派发侧失败数 ≥ threshold；
      2. **整轮全部**都是派发侧失败（有任何成功/邮箱侧失败即说明出口是通的）。
    """
    results = [r for r in (wave_results or []) if r is not None]
    if not results:
        return False
    signals = sum(1 for r in results if is_dispatch_side_failure(r))
    if signals < max(1, int(threshold)):
        return False
    return signals == len(results)


def wave_summary(wave_results: list) -> dict:
    results = [r for r in (wave_results or []) if r is not None]
    dispatch = sum(1 for r in results if is_dispatch_side_failure(r))
    success = sum(1 for r in results if isinstance(r, dict) and r.get('success'))
    return {
        'total': len(results),
        'success': success,
        'dispatch_side': dispatch,
        'mailbox_side': sum(1 for r in results
                            if any(m in _text_of(r) for m in MAILBOX_MARKERS)),
        'exit_ban_evidence': detect_exit_ban(results),
    }
