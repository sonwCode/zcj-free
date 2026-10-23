# -*- coding: utf-8 -*-
"""断点恢复：账号已建好但会话没拿到时，重试直接落库。

场景：
  协议注册走到 create_account 成功，但紧接着取 session / AT 失败。
  此时邮箱已在 OpenAI 侧被占用，重走注册会撞 user_already_exists；
  而账号其实已经存在，只是我们没存。

做法：
  protocol_registration 在各阶段写断点（otp_validated / account_created / access_token）；
  重试时先看断点，若 account_created 且有 access_token，直接落库并补齐 2FA/套餐入队，
  不再走整条注册链路。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def try_resume(email: str) -> dict | None:
    """尝试用断点直接落库；不可恢复时返回 None。"""
    addr = str(email or '').strip()
    if not addr:
        return None
    try:
        from config import register as _rc

        if not bool(getattr(_rc, 'PROTOCOL_CHECKPOINT_ENABLED', True)):
            return None
    except Exception:
        return None
    try:
        from core.registration_checkpoint import clear as _clear
        from core.registration_checkpoint import load as _load
        from core.registration_checkpoint import resumable as _resumable
    except Exception:
        return None

    info = _resumable(addr)
    if not info.get('resumable'):
        return None
    token = str(info.get('access_token') or '').strip()
    password = str(info.get('password') or '').strip()
    data = _load(addr)
    try:
        from core import db

        account_id = db.insert_account(
            email=addr,
            access_token=token,
            plan_type='free',
            email_source=data.get('email_source') or None,
            device_id=data.get('device_id') or None,
            proxy_used=data.get('proxy_used') or None,
            extra={
                'registration_password': password,
                'registration_driver': 'protocol',
                'resumed_from_checkpoint': True,
            },
            codex_status='skipped',
        )
    except Exception as exc:
        logger.warning('[断点恢复] %s 落库失败：%s', addr, str(exc)[:140])
        return None

    logger.info('[断点恢复] %s 已从断点落库 account_id=%s', addr, account_id)
    try:
        from config import twofa as _twofa_cfg

        if bool(getattr(_twofa_cfg, 'ENABLE_2FA', False)):
            from core.twofa_service import enqueue_account_totp_setup

            enqueue_account_totp_setup(account_id=int(account_id), email=addr,
                                       access_token=token, trigger='resume_auto')
    except Exception:
        pass
    try:
        from core import plan_check_service

        plan_check_service.enqueue_account_plan_check(
            account_id=int(account_id), email=addr, access_token=token,
            trigger='resume_auto',
        )
    except Exception:
        pass
    _clear(addr)
    return {
        'success': True,
        'task_status': 'success',
        'account_id': account_id,
        'email': addr,
        'outcome': 'success',
        'resumed': True,
        'network_traffic': None,
        'error': None,
    }
