#!/usr/bin/env python3
"""从本地 codex_accounts/*.json 回灌 Codex 凭据到 SQLite。

背景：凭据原本只存 SQLite，CPA 侧出问题或库行被覆盖就无副本可用。
现在保存时会另写一份完整本地文件；本工具负责反向：
  * 扫描本地文件，与库里的 filename 比对；
  * --apply 时把缺失的重写回库（不覆盖已有行，除非 --overwrite）。

用法：
    python tools/restore_codex_credentials.py            # 只报告
    python tools/restore_codex_credentials.py --apply    # 回灌缺失
    python tools/restore_codex_credentials.py --apply --overwrite
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _local_files() -> list:
    from core.codex_credential_store import _dir

    base = _dir()
    return sorted(base.glob('*.json')) if base.is_dir() else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core import db

    files = _local_files()
    if not files:
        print('本地目录没有凭据文件')
        return 0
    rows = []
    for fn in ('list_codex_accounts', 'list_codex_accounts_page'):
        fn_obj = getattr(db, fn, None)
        if not callable(fn_obj):
            continue
        try:
            got = fn_obj()
        except TypeError:
            continue
        except Exception:
            continue
        if isinstance(got, dict):
            got = got.get('items') or got.get('rows') or []
        rows = got or []
        if rows:
            break
    known = {str(r.get('filename') or '') for r in rows}
    missing = [p for p in files if p.name not in known]
    print('本地文件 %d 个，库内 %d 行，缺失 %d 个' % (len(files), len(known), len(missing)))
    for p in missing[:10]:
        print('  + %s' % p.name)
    if not args.apply:
        print('\n[dry-run] 加 --apply 回灌')
        return 0
    restored = 0
    for p in missing:
        try:
            content = json.loads(p.read_text(encoding='utf-8'))
        except Exception as exc:
            print('  跳过 %s（解析失败：%s）' % (p.name, exc))
            continue
        try:
            db.upsert_codex_credential(content, p.name)
            restored += 1
        except Exception as exc:
            print('  失败 %s：%s' % (p.name, exc))
    print('已回灌 %d 个' % restored)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
