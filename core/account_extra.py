# -*- coding: utf-8 -*-
"""更新账号 extra 字段（合并写入，不覆盖其它键）。

db.py 里没有通用的 extra 更新接口，只有若干专用 update_account_xxx。
这里实现一个最小合并写入：读 payload、合并 extra_json、写回。
用于把查活时抓到的 session-token cookie 存进账号，供后续 cookie 方式授权 Codex。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def update_extra(acc_id: int, patch: dict) -> bool:
    """把 patch 合并进账号 extra_json；成功返回 True。"""
    if not isinstance(patch, dict) or not patch:
        return False
    try:
        from core import db
    except Exception:
        return False
    lock = getattr(db, '_LOCK', None)
    if lock is None:
        return False
    with lock:
        accounts = db._load_accounts()
        row = next((r for r in accounts if int(r.get('id') or 0) == int(acc_id)), None)
        if row is None:
            return False
        try:
            extra = json.loads(row.get('extra_json') or '{}')
        except Exception:
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        extra.update(patch)
        row['extra_json'] = json.dumps(extra, ensure_ascii=False)
        try:
            db._save_accounts(accounts)
        except Exception as exc:
            logger.warning('[extra] 保存失败 #%s：%s', acc_id, str(exc)[:120])
            return False
    return True
