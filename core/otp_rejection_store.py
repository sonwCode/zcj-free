# -*- coding: utf-8 -*-
"""OTP 拒绝记录：记住被服务端拒过的验证码，避免重试时反复提交它。

来源：ZRainbow1275/AutoTeam-F 的 `_load_recent_otp_rejections` /
`_record_otp_rejection`（按 email+code 的 sha256 持久化，带 TTL）。

我们的问题：一次流程里 `exclude_otp_codes` 只在内存中生效；
**跨任务/跨进程再跑时，会把同一个已被拒的验证码又拿去提交**，
常见的后果是再吃一次 401/409，白等一轮取码时间。

设计：
  * 只存哈希（不存明文验证码，避免落盘敏感数据）；
  * 按邮箱分组，每条形如 {h: <sha256前32>, t: <ts>}；
  * 默认 TTL 6 小时，过期自动清掉。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

STORE = Path('/opt/turb-gpt-register/data/otp_rejections.json')
TTL_SECONDS = 6 * 3600
_LOCK = threading.RLock()


def _email_key(email: str) -> str:
    return str(email or '').strip().lower()


def _digest(email: str, code: str) -> str:
    raw = '%s:%s' % (_email_key(email), str(code or '').strip())
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
    except Exception:
        logger.debug('[OTP拒绝] 写入失败')


def note_rejection(email: str, code: str, path: Path | None = None,
                   now: float | None = None) -> bool:
    """记录一个被拒的验证码（哈希）。"""
    key = _email_key(email)
    if not key or not str(code or '').strip():
        return False
    target = Path(path or STORE)
    stamp = time.time() if now is None else float(now)
    with _LOCK:
        data = _load(target)
        items = [x for x in (data.get(key) or [])
                 if isinstance(x, dict) and stamp - float(x.get('t') or 0) < TTL_SECONDS]
        digest = _digest(key, code)
        if not any(str(x.get('h')) == digest for x in items):
            items.append({'h': digest, 't': stamp})
        data[key] = items[-20:]
        _save(target, data)
    return True


def recent_digests(email: str, path: Path | None = None,
                   now: float | None = None) -> set:
    """返回该邮箱近期被拒验证码的哈希集合。"""
    target = Path(path or STORE)
    stamp = time.time() if now is None else float(now)
    with _LOCK:
        data = _load(target)
        items = data.get(_email_key(email)) or []
    return {str(x.get('h')) for x in items
            if isinstance(x, dict) and stamp - float(x.get('t') or 0) < TTL_SECONDS}


def is_rejected(email: str, code: str, path: Path | None = None,
                now: float | None = None) -> bool:
    return _digest(email, code) in recent_digests(email, path, now)


def filter_candidates(email: str, codes, path: Path | None = None,
                      now: float | None = None) -> list:
    """从候选验证码中剔除近期被拒过的。"""
    bad = recent_digests(email, path, now)
    out = []
    for c in codes or []:
        if _digest(email, c) in bad:
            continue
        out.append(c)
    return out


def clear(email: str, path: Path | None = None) -> None:
    target = Path(path or STORE)
    with _LOCK:
        data = _load(target)
        if data.pop(_email_key(email), None) is not None:
            _save(target, data)
