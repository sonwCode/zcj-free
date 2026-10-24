#!/usr/bin/env python3
"""把密码保险箱里遗留的密码回填到账号（注册曾失败但有密码的场景）。

用法：
    python tools/backfill_pending_passwords.py            # 只报告
    python tools/backfill_pending_passwords.py --apply
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core import db, password_vault

    pending = password_vault.list_pending()
    print('保险箱条目:', len(pending))
    if not pending:
        return 0
    fixed, missing = [], []
    for email, entry in pending.items():
        pwd = str((entry or {}).get('password') or '')
        if not pwd:
            continue
        acc = db.get_account_by_email(email)
        if not acc:
            missing.append(email)
            continue
        try:
            extra = json.loads(acc.get('extra_json') or '{}')
        except Exception:
            extra = {}
        if str(extra.get('registration_password') or ''):
            continue
        fixed.append((email, int(acc.get('id') or 0)))
        if args.apply:
            extra['registration_password'] = pwd
            extra['password_backfilled'] = True
            from core.account_extra import update_extra

            update_extra(int(acc.get('id') or 0),
                         {'registration_password': pwd, 'password_backfilled': True})
            password_vault.pop(email)
    print('可回填:', len(fixed), '| 账号已不在库:', len(missing))
    for email, acc_id in fixed[:10]:
        print('  #%-5s %s' % (acc_id, email))
    if not args.apply:
        print('\n[dry-run] 加 --apply 执行')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
