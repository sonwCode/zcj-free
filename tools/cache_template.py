#!/usr/bin/env python3
"""维护缓存预热模板：从现有 profile 里挑最大最新的一份缓存，复制成模板。

用法：
    python tools/cache_template.py            # 刷新模板
    python tools/cache_template.py --dry-run  # 只看会选哪个
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path('/opt/turb-gpt-register')
PROFILES = ROOT / 'cloak-profiles'
TEMPLATE = ROOT / 'cloak-cache-template'
SUBDIRS = ('Default/Cache', 'Default/Code Cache', 'Default/GPUCache')
MIN_BYTES = 8 * 1024 * 1024


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob('*'):
        try:
            if item.is_file():
                total += item.stat().st_size
        except Exception:
            continue
    return total


def pick_best() -> tuple[Path, int] | None:
    best = None
    if not PROFILES.exists():
        return None
    for profile in PROFILES.iterdir():
        cache = profile / 'Default' / 'Cache'
        if not cache.is_dir():
            continue
        size = _dir_size(cache)
        if size < MIN_BYTES:
            continue
        mtime = cache.stat().st_mtime
        key = (size, mtime)
        if best is None or key > best[2]:
            best = (profile, size, key)
    if best is None:
        return None
    return best[0], best[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    picked = pick_best()
    if not picked:
        print('没有找到足够大的缓存（阈值 8MB）')
        return 1
    profile, size = picked
    print('选中模板: %s（缓存 %.1f MB）' % (profile.name, size / 1048576))
    if args.dry_run:
        return 0
    TEMPLATE.mkdir(parents=True, exist_ok=True)
    copied = 0
    for sub in SUBDIRS:
        src = profile / sub
        if not src.is_dir():
            continue
        dst = TEMPLATE / sub
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        copied += 1
    total = _dir_size(TEMPLATE)
    print('模板已刷新: %s（%d 个目录，%.1f MB）' % (TEMPLATE, copied, total / 1048576))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
