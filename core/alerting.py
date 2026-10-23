# -*- coding: utf-8 -*-
"""变化告警：把"账号批量失效"这类事件主动推出去。

借鉴 Regert888/upstream-ratio-watch：它用线程调度定时采集，
并在数值变化时推送到企业微信 webhook / QQ 群机器人。

我们的缺口：查活能算出"这批号里死了几个"，但**没有人被告知**——
要等人工打开界面才发现。

本模块只做三件事：
  * 组装各渠道的 payload（企业微信 / 通用 webhook / Telegram）；
  * 节流：同一个 key 在窗口内只发一次（状态落盘，跨进程有效）；
  * 阈值判断：死亡比例超过阈值才发。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path('/opt/turb-gpt-register/alert_state.json')


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8')) or {}
    except Exception:
        return {}


def _save_state(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
    except Exception:
        pass


def should_send(key: str, min_interval: float = 1800.0,
                path: Path | None = None, now: float | None = None) -> bool:
    """同一 key 在 min_interval 秒内只允许发一次。"""
    target = Path(path or STATE_FILE)
    stamp = float(now if now is not None else time.time())
    state = _load_state(target)
    last = float(state.get(key) or 0)
    if last and (stamp - last) < float(min_interval):
        return False
    state[key] = stamp
    _save_state(target, state)
    return True


def build_wecom(text: str) -> dict:
    """企业微信群机器人 payload。"""
    return {'msgtype': 'text', 'text': {'content': str(text)[:1800]}}


def build_generic(text: str) -> dict:
    """通用 webhook payload（兼容多数自建通知服务）。"""
    return {'text': str(text)[:1800]}


def build_telegram(text: str, chat_id: str) -> dict:
    """Telegram Bot API payload。"""
    return {'chat_id': str(chat_id), 'text': str(text)[:3500]}


def send(text: str, webhook: str, channel: str = 'wecom',
         chat_id: str = '', timeout: float = 8.0, sender=None) -> dict:
    """发送告警；sender 可注入用于测试。"""
    url = str(webhook or '').strip()
    if not url:
        return {'ok': False, 'error': '未配置 webhook'}
    payload = {
        'wecom': build_wecom(text),
        'telegram': build_telegram(text, chat_id),
    }.get(str(channel or 'wecom').lower(), build_generic(text))
    if sender is not None:
        try:
            sender(url, payload)
            return {'ok': True, 'channel': channel}
        except Exception as exc:
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    try:
        import requests

        resp = requests.post(url, json=payload, timeout=timeout)
        return {'ok': resp.status_code < 400, 'status': resp.status_code}
    except Exception as exc:
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}


def dead_ratio_exceeded(total: int, dead: int, threshold: float = 0.3) -> bool:
    """死亡比例是否超过阈值（样本太小不告警）。"""
    try:
        n = int(total or 0)
        d = int(dead or 0)
    except Exception:
        return False
    if n < 5:
        return False
    return (d / float(n)) >= float(threshold)
