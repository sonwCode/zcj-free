# -*- coding: utf-8 -*-
"""已用手机号登记表：一条号码只能验一个号，重复号码必然白跑。

依据 OpenAI 官方说明（由 wenfxl/openai-cpa 的启动横幅转述）：
  「创建账号时验证手机号，**一个号码只能验证一个账号**」

这条约束解释了我们的现象：接码平台（smsbower/tiger）重试 10 次仍失败，
因为平台会反复分配被用过的号码，而这类号码在 OpenAI 侧必然被拒。

本模块做最小闭环：记录用过的号码（E.164 归一），取号后先查再提交，
命中就取消并换号，而不是把一次配额浪费在注定失败的号码上。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STATE = Path('/opt/turb-gpt-register/data/used_phone_numbers.json')
_LOCK = threading.RLock()


def normalize(number: str) -> str:
    """归一成纯数字（保留国家码，去掉 +、空格、连字符）。"""
    digits = re.sub(r'\D', '', str(number or ''))
    return digits


def _load(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
    except Exception:
        logger.debug('[号码] 状态写入失败')


def is_used(number: str, path: Path | None = None) -> bool:
    key = normalize(number)
    if not key:
        return False
    with _LOCK:
        return key in _load(Path(path or STATE))


def mark_used(number: str, email: str = '', path: Path | None = None) -> bool:
    """登记一个号码（幂等）。"""
    key = normalize(number)
    if not key:
        return False
    target = Path(path or STATE)
    with _LOCK:
        data = _load(target)
        if key in data:
            return False
        data[key] = {'email': str(email or ''), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        _save(target, data)
    return True


def filter_unused(numbers, path: Path | None = None) -> list:
    """从号码列表里剔除用过的。"""
    target = Path(path or STATE)
    with _LOCK:
        data = _load(target)
    out = []
    for item in numbers or []:
        if normalize(item) not in data:
            out.append(item)
    return out


def stats(path: Path | None = None) -> dict:
    target = Path(path or STATE)
    with _LOCK:
        data = _load(target)
    return {'used': len(data), 'path': str(target)}
