#!/usr/bin/env python3
"""数据库快照：把 turb.sqlite3 备份到 backups/db-*.sqlite3，保留最近 N 份。

用法：
    python tools/backup_db.py              # 备份一份，保留 14 份
    python tools/backup_db.py --keep 30
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


def backup(keep: int = 14) -> dict:
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
    for old in files[:-max(1, int(keep))]:
        try:
            old.unlink()
            removed += 1
        except Exception:
            pass
    return {'ok': True, 'path': str(target), 'removed': removed, 'count': len(files)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', type=int, default=14)
    args = parser.parse_args()
    result = backup(args.keep)
    print(result)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
