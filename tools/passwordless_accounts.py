#!/usr/bin/env python3
"""列出没有注册密码的账号（便于决定补密码还是弃用）。

用法：
    python tools/passwordless_accounts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import config as cfg
    cfg.reload_all()
    from core import db
    from core.password_state import list_missing, password_state

    # 注意：db.list_accounts 默认 archived=False，会漏掉归档账号。
    # 这里必须取全量（archived=None），否则 15 个归档号会被当成"不存在"。
    rows = db.list_accounts(limit=100000, archived=None) or []
    missing = list_missing(rows)
    print('账号总数: %d' % len(rows))
    print('无密码: %d' % len(missing))
    for a in missing[:50]:
        extra = {}
        try:
            extra = json.loads(a.get('extra_json') or '{}')
        except Exception:
            extra = {}
        print('  #%-5s %-34s totp=%s live=%s created=%s'
              % (a.get('id'), str(a.get('email'))[:34],
                 bool(a.get('totp_secret')),
                 a.get('live_check_status') or '-',
                 str(a.get('created_at'))[:16]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
