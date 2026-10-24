# -*- coding: utf-8 -*-
"""注册密码保险箱：密码一旦设上就立刻落盘，不等账号落库。

来源：Regert888 的 `webui/registrar._save_password_early(email, password)`。

我们的问题：`core/codex_oauth._PENDING_PASSWORDS` 是**内存字典**。
如果注册在"设好密码"之后失败（OTP 超时、建号失败、进程重启），
这个密码就永远丢了——账号存在但没有密码，
后续查活只能走 OTP、2FA/Codex 也更麻烦。

本模块把密码第一时间写进 `data/pending_passwords.json`（0600）：
  * 注册成功 → 落库时取走并删除；
  * 注册失败 → 留在保险箱里，下次同一邮箱的流程还能复用；
  * 可事后用 `tools/backfill_pending_passwords.py` 回填到已有账号。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT = Path('/opt/turb-gpt-register/data/pending_passwords.json')
_LOCK = threading.RLock()


def _key(email: str) -> str:
    return str(email or '').strip().lower()


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
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
    except Exception:
        logger.debug('[密码保险箱] 写入失败')


def remember(email: str, password: str, path: Path | None = None) -> bool:
    """立刻落盘一个注册密码（幂等覆盖）。"""
    key, pwd = _key(email), str(password or '').strip()
    if not key or not pwd:
        return False
    target = Path(path or VAULT)
    with _LOCK:
        data = _load(target)
        data[key] = {'password': pwd, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        _save(target, data)
    return True


def get(email: str, path: Path | None = None) -> str:
    with _LOCK:
        entry = _load(Path(path or VAULT)).get(_key(email)) or {}
    return str(entry.get('password') or '') if isinstance(entry, dict) else ''


def pop(email: str, path: Path | None = None) -> str:
    """取走并删除（账号落库后用）。"""
    key = _key(email)
    target = Path(path or VAULT)
    with _LOCK:
        data = _load(target)
        entry = data.pop(key, None)
        if entry is not None:
            _save(target, data)
    return str((entry or {}).get('password') or '') if isinstance(entry, dict) else ''


def list_pending(path: Path | None = None) -> dict:
    with _LOCK:
        return dict(_load(Path(path or VAULT)))
