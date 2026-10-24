#!/usr/bin/env python3
"""打印某账号的完整 Codex 凭据（可复制给 CPA / 客户端）。

优先读库，库内缺失时回退本地 codex_accounts/*.json。

用法：
    python tools/codex_credential_dump.py --email a@b.c
    python tools/codex_credential_dump.py --email a@b.c --save /tmp/auth.json
    python tools/codex_credential_dump.py --list
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _find_filename(email: str) -> str:
    from core import db

    key = str(email or '').strip().lower()
    rows = db.list_codex_accounts() or []
    for row in rows:
        name = str(row.get('filename') or '')
        if key and key in name.lower():
            return name
    return ''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', default='')
    parser.add_argument('--save', default='')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core import codex_credential_store as store
    from core import db

    if args.list:
        rows = db.list_codex_accounts() or []
        names = {str(r.get('filename') or '') for r in rows}
        local = set(store.list_files())
        print('库内 %d，本地 %d，仅在本地 %d' % (len(names), len(local), len(local - names)))
        for name in sorted(local):
            mark = '库' if name in names else '本地'
            print('  [%s] %s' % (mark, name))
        return 0

    if not args.email:
        print('需要 --email 或 --list')
        return 1
    fname = _find_filename(args.email)
    if not fname:
        print('库里找不到该邮箱的凭据')
        return 1
    try:
        content, name = db.read_codex_credential(fname)
    except Exception as exc:
        print('读取失败:', exc)
        return 1
    if args.save:
        Path(args.save).write_text(content, encoding='utf-8')
        print('已保存到', args.save, '(%s)' % name)
        return 0
    print(content)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
