# -*- coding: utf-8 -*-
"""注册断点：把已完成的阶段存盘，重试时不必从头再来。

借鉴 2951461586/GPT-Register-Tool 的 registration/checkpoint.py。

它要解决的问题：
  建号（create_account）成功后，若取会话/AT 失败，整轮被判失败；
  重试时又从头走 authorize/continue → user/register → OTP → about-you，
  而这时邮箱已经被占用，user/register 会返回 user_already_exists，
  于是重试反而更难成功。

断点把这几个阶段的结果落盘（按邮箱分文件）：
  authorize_continue / password_set / otp_validated / account_created
  + 已拿到的 access_token（若有）
重试时若 account_created=True 且 access_token 已存在，可直接落库，跳过整条链路。

设计约束：
  * 默认关闭（config.PROTOCOL_CHECKPOINT_ENABLED），避免改变既有行为；
  * 只保存"可复用的事实"，不保存会话对象/Cookie；
  * 文件权限 600，内容含密码，不能进日志。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path('/opt/turb-gpt-register/checkpoints')


def _safe_name(email: str) -> str:
    text = str(email or '').strip().lower()
    return ''.join(ch if ch.isalnum() or ch in '._@-' else '_' for ch in text)[:120]


def checkpoint_path(email: str, base_dir: str | Path | None = None) -> Path:
    root = Path(str(base_dir or DEFAULT_DIR))
    return root / ('%s.json' % _safe_name(email))


def load(email: str, base_dir: str | Path | None = None) -> dict:
    path = checkpoint_path(email, base_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8')) or {}
    except Exception:
        logger.debug('[断点] 读取失败：%s', path)
        return {}


def save(email: str, data: dict, base_dir: str | Path | None = None) -> Path:
    path = checkpoint_path(email, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data or {})
    payload['email'] = str(email or '')
    payload['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(path)
    return path


def update(email: str, **stages) -> dict:
    """写入/更新若干阶段标记，返回最新内容。"""
    data = load(email)
    data.update(stages)
    save(email, data)
    return data


def clear(email: str, base_dir: str | Path | None = None) -> bool:
    path = checkpoint_path(email, base_dir)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except Exception:
        return False


def resumable(email: str, base_dir: str | Path | None = None) -> dict:
    """判断该邮箱是否可以"跳过建号直接落库"。"""
    data = load(email, base_dir)
    token = str(data.get('access_token') or '').strip()
    if data.get('account_created') and token:
        return {
            'resumable': True,
            'access_token': token,
            'password': str(data.get('password') or ''),
            'reason': 'account_created+access_token',
        }
    return {'resumable': False, 'reason': 'insufficient' if data else 'no_checkpoint'}
