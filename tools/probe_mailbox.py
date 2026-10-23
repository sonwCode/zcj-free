#!/usr/bin/env python3
"""只读探测邮箱在 OpenAI 侧是否已注册（不改变任何账号状态）。

用法：
    python tools/probe_mailbox.py someone@outlook.com
    python tools/probe_mailbox.py a@x.com b@y.com --json
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
    parser.add_argument('emails', nargs='+')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core.mailbox_probe import probe_mailbox

    rows = []
    for email in args.emails:
        result = probe_mailbox(email, attempts=args.attempts)
        rows.append({'email': email, **result})
        if not args.json:
            print('%-40s %-12s %s' % (email, result.get('status'),
                                      result.get('note') or (result.get('landing') or '')[:50]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
