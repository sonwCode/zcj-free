#!/usr/bin/env python3
"""检测/回收被占用的浏览器 profile。

用法：
    python tools/reclaim_profiles.py                 # 只报告
    python tools/reclaim_profiles.py --terminate     # 终止浏览器类进程
    python tools/reclaim_profiles.py --json
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
    parser.add_argument('--terminate', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--root', default='')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core.profile_reclaim import reclaim_stale

    root = args.root
    if not root:
        from config import cloakbrowser as cb

        root = str(getattr(cb, 'CLOAK_USER_DATA_DIR', '') or '')
    if not root:
        print('没有配置 profile 根目录')
        return 1

    result = reclaim_stale(root, terminate=args.terminate)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if not result.get('ok'):
        print(result.get('error'))
        return 1
    print('profile 总数 %d，空闲 %d，被占用 %d' % (
        result['total'], result['clean'], len(result['busy'])))
    for item in result['busy'][:20]:
        pids = ','.join(str(h['pid']) for h in item['holders'])
        kinds = ','.join('B' if h.get('browser_like') else 'x' for h in item['holders'])
        print('  %-52s 占用进程 %s (%s)' % (item['profile'][:52], pids, kinds))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
