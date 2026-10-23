#!/usr/bin/env python3
"""AT 存活曲线看板（移植自 2951461586/GPT-Register-Tool 的 at_survival_watch.py 思路）。

用途：新注册的号到底能活多久，用数据说话。

用法：
    python tools/at_survival_watch.py --ids 217 218 219 --label remail
    python tools/at_survival_watch.py --since-minutes 30 --label all

判定：
    200 = 存活
    401 / token_revoked = 已撤销（记 401）
    其它 / 网络异常 = 0（不计入存活率分母）
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

API = 'https://chatgpt.com/backend-api/me'
SCHEDULE = (1, 5, 15, 30, 60)


def _probe(account: dict) -> str:
    token = str(account.get('access_token') or '').strip()
    if not token:
        return '0'
    try:
        resp = requests.get(
            API,
            headers={'Authorization': 'Bearer ' + token,
                     'Accept': 'application/json',
                     'User-Agent': 'Mozilla/5.0'},
            timeout=15,
        )
    except Exception:
        return '0'
    if resp.status_code == 200:
        return '200'
    body = (resp.text or '')[:200].lower()
    if resp.status_code == 401 or 'token_revoked' in body:
        return '401'
    return str(resp.status_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ids', nargs='*', type=int, help='指定账号 id')
    parser.add_argument('--since-minutes', type=int, default=0,
                        help='取最近 N 分钟注册的账号')
    parser.add_argument('--label', default='run', help='这轮标签，写进输出')
    parser.add_argument('--out', default='/tmp/at_survival.jsonl')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core import db

    accounts = db.list_accounts(limit=100000) or []
    if args.ids:
        wanted = set(args.ids)
        accounts = [a for a in accounts if int(a.get('id') or 0) in wanted]
    elif args.since_minutes > 0:
        cutoff = time.time() - args.since_minutes * 60
        kept = []
        for acc in accounts:
            raw = str(acc.get('created_at') or '')[:19]
            try:
                ts = time.mktime(time.strptime(raw, '%Y-%m-%dT%H:%M:%S'))
            except Exception:
                continue
            if ts >= cutoff:
                kept.append(acc)
        accounts = kept
    else:
        print('需要 --ids 或 --since-minutes', file=sys.stderr)
        return 2

    if not accounts:
        print('没有匹配的账号', file=sys.stderr)
        return 1

    print('监控 %d 个账号，标签=%s' % (len(accounts), args.label))
    t0 = time.time()
    rows = []
    for minutes in SCHEDULE:
        wait = t0 + minutes * 60 - time.time()
        if wait > 0:
            time.sleep(wait)
        with ThreadPoolExecutor(max_workers=8) as pool:
            verdicts = list(pool.map(_probe, accounts))
        counted = [v for v in verdicts if v != '0']
        alive = sum(1 for v in counted if v == '200')
        row = {
            'label': args.label,
            'minute': minutes,
            'total': len(accounts),
            'alive': alive,
            'counted': len(counted),
            'detail': {int(a.get('id') or 0): v for a, v in zip(accounts, verdicts)},
        }
        rows.append(row)
        with open(args.out, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        print('+%2dmin  存活 %d/%d  %s' % (minutes, alive, len(counted), row['detail']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
