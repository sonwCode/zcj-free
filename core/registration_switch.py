# -*- coding: utf-8 -*-
"""注册总开关：暂停后不再接受任何新的注册任务。

用户要求："为什么在持续的注册，这个应该做的开关吧在注册页"

背景：重启补偿（requeue）会按批提交注册，看起来像"自己在跑"。
这里提供一个运行时可切的开关，落盘到 data/registration_switch.json，
不依赖改配置重启。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE = Path('/opt/turb-gpt-register/data/registration_switch.json')


def _load() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(STATE)
    except Exception:
        logger.debug('[注册开关] 写入失败')


def status() -> dict:
    data = _load()
    return {
        'paused': bool(data.get('paused', False)),
        'auto_submit': bool(data.get('auto_submit', True)),
        'split': bool(data.get('split', False)),
        'updated_at': str(data.get('updated_at') or ''),
        'reason': str(data.get('reason') or ''),
    }


def is_paused() -> bool:
    return bool(_load().get('paused', False))


def split_enabled() -> bool:
    """注册时是否启用"分裂注册"（为邮箱派生 +子地址）。"""
    return bool(_load().get('split', False))


def set_state(paused: bool | None = None, auto_submit: bool | None = None,
              split: bool | None = None, reason: str = '') -> dict:
    data = _load()
    if paused is not None:
        data['paused'] = bool(paused)
    if auto_submit is not None:
        data['auto_submit'] = bool(auto_submit)
    if split is not None:
        data['split'] = bool(split)
    data['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    if reason:
        data['reason'] = str(reason)[:120]
    _save(data)
    logger.warning('[注册开关] paused=%s auto_submit=%s split=%s (%s)',
                   data.get('paused'), data.get('auto_submit'), data.get('split'), reason or '-')
    return status()
