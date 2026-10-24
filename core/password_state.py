# -*- coding: utf-8 -*-
"""密码状态：把"这个号需要密码，但我们没存"当成独立状态。

来源：starmiaoa 的 `login/login_flow.py` 里有个 `PasswordRequiredError(LoginError)`——
它把"需要密码而当前没有"从"登录失败"里拆出来，作为可单独统计的错误类型。

我们为什么需要：库里有 13 个无密码账号（注册时走了无密码分支），
它们登录失败时只会表现为笼统的"登录失败"，无法被单独筛出、
也无法判断是"号坏了"还是"我们缺密码"。

本模块只做判定与查询，不改变现有流程：
  password_state(account)  -> 'set' | 'missing'
  has_password(account)    -> bool
  require_password(account) -> 缺失时抛 PasswordRequired
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

SET = 'set'
MISSING = 'missing'


class PasswordRequired(RuntimeError):
    """该账号需要密码，但我们没有存。"""

    def __init__(self, email: str = '', account_id=None):
        self.email = str(email or '')
        self.account_id = account_id
        super().__init__('账号需要密码但未保存：%s' % (self.email or account_id or '?'))


def _extra(account) -> dict:
    raw = (account or {}).get('extra_json') if isinstance(account, dict) else None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith('{'):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def stored_password(account) -> str:
    if not isinstance(account, dict):
        return ''
    direct = str(account.get('registration_password') or '').strip()
    if direct:
        return direct
    return str(_extra(account).get('registration_password') or '').strip()


def has_password(account) -> bool:
    return bool(stored_password(account))


def password_state(account) -> str:
    return SET if has_password(account) else MISSING


def require_password(account) -> str:
    """返回密码；缺失时抛 PasswordRequired（调用方可按类型单独处理）。"""
    pwd = stored_password(account)
    if pwd:
        return pwd
    raise PasswordRequired(
        str((account or {}).get('email') or ''),
        (account or {}).get('id'),
    )


def list_missing(accounts) -> list:
    """从账号列表里筛出没有密码的（用于批量处理/报表）。"""
    return [a for a in (accounts or []) if not has_password(a)]
