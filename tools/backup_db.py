#!/usr/bin/env python3
"""数据库快照：把 turb.sqlite3 备份到 backups/db-*.sqlite3，保留最近 N 份。

用法：
    python tools/backup_db.py               # 备份一份，保留最近 3 天
    python tools/backup_db.py --days 7      # 改保留天数
    python tools/backup_db.py --keep 20     # 或改保留份数（两者取交集）
"""
from __future__ import annotations
import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'turb.sqlite3'
OUT = ROOT / 'backups'


def backup(keep: int = 0, days: int = 3) -> dict:
    if not DB.is_file():
        return {'ok': False, 'error': '数据库不存在'}
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    target = OUT / ('db-%s.sqlite3' % stamp)
    try:
        src = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except Exception as exc:
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    files = sorted(OUT.glob('db-*.sqlite3'))
    removed = 0
    now = time.time()
    keep_set = set()
    if int(keep or 0) > 0:
        keep_set = set(files[-int(keep):])
    for old in files:
        if old == target or old in keep_set:
            continue
        try:
            age_days = (now - old.stat().st_mtime) / 86400.0
        except Exception:
            continue
        if age_days <= max(0.0, float(days)):
            continue
        try:
            old.unlink()
            removed += 1
        except Exception:
            pass
    remain = sorted(OUT.glob('db-*.sqlite3'))
    return {'ok': True, 'path': str(target), 'removed': removed, 'count': len(remain),
            'days': days}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', type=int, default=0)
    parser.add_argument('--days', type=int, default=3)
    args = parser.parse_args()
    result = backup(keep=args.keep, days=args.days)
    print(result)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
