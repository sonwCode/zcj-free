# -*- coding: utf-8 -*-
"""注册时分列：为选中的邮箱派生一个 +子地址用于注册。

（用户要求：不在导入时分列，而是在注册页开一个开关，注册时派生。）

规则：
  * 开关来自 core.registration_switch.split_enabled()；
  * 仅对 imap 来源生效（其他来源没有统一收件箱语义）；
  * 地址已含 + 的直接原样返回；
  * 派生出的子地址会先落进 imap 池（继承父行登录名/密码/服务器），
    这样取码时 get_account_context 能找到它，按 To 头匹配也能命中。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _has_plus(email: str) -> bool:
    local, _, _domain = str(email or "").partition("@")
    return "+" in local


def maybe_split(email: str, source: str) -> str:
    """按开关决定是否返回子地址；任何异常都退回原地址（不影响注册）。"""
    addr = str(email or "").strip()
    if not addr:
        return addr
    try:
        from core import registration_switch

        if not registration_switch.split_enabled():
            return addr
    except Exception:
        return addr
    if str(source or "").lower() != "imap" or _has_plus(addr):
        return addr
    try:
        from core import db
        from core.email_splitter import split_email

        parent = db.get_imap_email_by_email(addr) or {}
        alias = split_email(addr, 1)[0]
        record = {
            "email": alias,
            "imap_password": parent.get("imap_password") or "",
            "imap_server": parent.get("imap_server") or "",
            "imap_port": parent.get("imap_port") or 993,
            "imap_ssl": parent.get("imap_ssl", True),
            "imap_username": str(parent.get("imap_username") or "").strip() or addr,
        }
        inserted, skipped = db.import_imap_emails([record])
        logger.info("[分裂注册] %s -> %s（入库 %d / 跳过 %d）", addr, alias, inserted, skipped)
        return alias
    except Exception as exc:
        logger.warning("[分裂注册] 派生失败，退回原地址：%s: %s", type(exc).__name__, str(exc)[:120])
        return addr