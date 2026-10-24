# -*- coding: utf-8 -*-
"""Codex 凭据的本地完整副本。

现状问题（用户反馈）：凭据只写进 SQLite（返回 `sqlite://codex_accounts/xxx.json`），
一旦 CPA 那边出问题、或者库里的行被覆盖/清理，就没有第二份可用副本。

本模块在每次保存凭据时**再写一份真实文件**到 `codex_accounts/`：
  * 文件名与 CLIProxyAPI 一致（codex-<email>[-plan].json），可直接喂给 CPA；
  * 权限 0600（含 refresh_token，等同于密码）；
  * 附带 sha256 与写入时间，便于校验"本地副本是否最新"；
  * `load()` 在库里查不到时可作为回退来源。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path('/opt/turb-gpt-register')


def _dir() -> Path:
    try:
        from config import codex as cc

        name = str(getattr(cc, 'CODEX_OUTPUT_DIRNAME', 'codex_accounts') or 'codex_accounts')
    except Exception:
        name = 'codex_accounts'
    return ROOT / name


def _safe_name(filename: str) -> str:
    name = str(filename or '').strip().replace('/', '_').replace('\\', '_')
    return name or ('codex-unknown-%d.json' % int(time.time()))


def save(content: dict, filename: str) -> dict:
    """写本地完整副本；返回 {path, sha256, bytes}。"""
    if not isinstance(content, dict) or not content:
        return {'ok': False, 'error': '内容为空'}
    target_dir = _dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {'ok': False, 'error': '目录不可用：%s' % exc}
    path = target_dir / _safe_name(filename)
    body = json.dumps(content, ensure_ascii=False, indent=1)
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(body, encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
    except Exception as exc:
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    logger.info('[凭据] 已写本地副本 %s（%d 字节，sha256=%s…）',
                path.name, len(body), digest[:12])
    return {'ok': True, 'path': str(path), 'sha256': digest, 'bytes': len(body)}


def load(filename: str) -> dict:
    """按文件名读本地副本；不存在返回空。"""
    path = _dir() / _safe_name(filename)
    try:
        return json.loads(path.read_text(encoding='utf-8')) or {}
    except Exception:
        return {}


def exists(filename: str) -> bool:
    return (_dir() / _safe_name(filename)).is_file()


def list_files() -> list:
    try:
        return sorted(p.name for p in _dir().glob('*.json'))
    except Exception:
        return []


def stats() -> dict:
    files = list_files()
    try:
        total = sum((_dir() / name).stat().st_size for name in files)
    except Exception:
        total = 0
    return {'count': len(files), 'bytes': total, 'dir': str(_dir())}
