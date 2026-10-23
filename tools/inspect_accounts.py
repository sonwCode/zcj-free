#!/usr/bin/env python3
"""账号巡检：本地 JWT 过期判定 + 轻量网络探活（每号 1 个请求）。

用法：
    python tools/inspect_accounts.py --local
    python tools/inspect_accounts.py --network --retry-unknown
    python tools/inspect_accounts.py --network --update-db
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--retry-unknown', action='store_true')
    parser.add_argument('--update-db', action='store_true')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--ids', nargs='*', type=int)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    if not (args.local or args.network):
        args.local = True

    import config as cfg
    cfg.reload_all()
    from core.account_inspection import inspect_local, inspect_network

    if args.local:
        report = inspect_local(account_ids=args.ids)
        data = report.as_dict()
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        else:
            print('本地巡检: 总数 %(total)s 有效 %(valid)s 即将过期 %(expiring)s 已过期 %(expired)s 无效 %(invalid)s 已废跳过 %(deactivated_skipped)s' % data)

    if args.network:
        result = inspect_network(account_ids=args.ids, workers=args.workers, update_db=args.update_db)
        if args.retry_unknown:
            pending = [item['id'] for item in result['detail']['unknown']]
            if pending:
                time.sleep(2)
                again = inspect_network(account_ids=pending, workers=2, update_db=args.update_db)
                result['detail']['unknown'] = [i for i in result['detail']['unknown'] if i['id'] not in pending]
                for verdict in ('alive', 'revoked'):
                    result['detail'][verdict].extend(again['detail'][verdict])
        summary = {k: len(v) for k, v in result['detail'].items()}
        summary['total'] = len(result['detail']['alive']) + len(result['detail']['revoked']) + len(result['detail']['unknown'])
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        else:
            print('网络巡检: 总数 %d 存活 %d 吊销 %d 未知 %d' % (summary['total'], summary['alive'], summary['revoked'], summary['unknown']))
            for verdict in ('revoked', 'unknown'):
                for item in result['detail'][verdict][:20]:
                    print('  %-8s #%-5s %s' % (verdict, item['id'], item['email'][:36]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
