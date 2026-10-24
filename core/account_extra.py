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
    """把 patch 合并进账号 extra_json（单行 UPDATE，绝不整表回写）。

    历史教训：早先的实现是"读出全表 → 改 → 整表写回"，
    当外部脚本与线上服务并发时会互相覆盖——服务刚插入的新账号
    会被脚本用旧快照整表覆盖掉，造成批量数据丢失。
    这里改为只 UPDATE 目标行，避免任何跨行影响。
    """
    if not isinstance(patch, dict) or not patch:
        return False
    try:
        from core import db
    except Exception:
        return False
    conn = getattr(db, '_sqlite_conn', None)
    if conn is None:
        return False
    try:
        with conn() as handle:
            row = handle.execute('select payload from accounts where id=?', (int(acc_id),)).fetchone()
            if row is None:
                return False
            try:
                payload = json.loads(row[0] or '{}')
            except Exception:
                payload = {}
            extra = payload.get('extra_json')
            if isinstance(extra, str) and extra.strip().startswith('{'):
                try:
                    merged = json.loads(extra)
                except Exception:
                    merged = {}
            else:
                merged = {}
            merged.update(patch)
            payload['extra_json'] = json.dumps(merged, ensure_ascii=False)
            handle.execute('update accounts set payload=? where id=?',
                           (json.dumps(payload, ensure_ascii=False), int(acc_id)))
        return True
    except Exception as exc:
        logger.warning('[extra] 单行更新失败 #%s：%s', acc_id, str(exc)[:120])
        return False
