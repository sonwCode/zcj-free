# -*- coding: utf-8 -*-
"""浏览器 profile 占用检测与回收。

借鉴 2951461586/GPT-Register-Tool 的 browser_profile_reclaim.py。
浏览器驱动用持久 profile（user_data_dir），目录按账号分。父进程被杀后，
浏览器进程可能活着并占着 profile，下一次在同目录启动就报 profile is already in use，
而这条报错不会告诉你到底是谁占着。

两条关键判断（照搬）：
  1. 看 lock 文件没用 —— parent.lock 启动时创建、正常退出也会留下；
  2. 唯一可移植的答案是找命令行里引用了该 profile 目录的活进程。

终止是显式选项，并且带镜像名保护：只有看起来像浏览器的进程才会被杀；
仅仅提到这个路径的进程（备份、grep）只报告、不杀。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

BROWSER_IMAGE_HINTS = (
    'chrome', 'chromium', 'chromedriver', 'playwright', 'cloakbrowser',
    'firefox', 'camoufox', 'headless_shell',
)


def _iter_proc_cmdlines():
    """遍历 /proc，产出 (pid, comm, cmdline)。非 Linux 返回空。"""
    proc = Path('/proc')
    if not proc.is_dir():
        return
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except Exception:
            continue
        cmdline = raw.replace(b'\x00', b' ').decode('utf-8', 'ignore')
        if not cmdline.strip():
            continue
        try:
            comm = (entry / 'comm').read_text(encoding='utf-8', errors='ignore').strip()
        except Exception:
            comm = ''
        yield int(entry.name), comm, cmdline


def find_holders(profile_dir: str) -> list:
    """返回命令行里引用该 profile 目录的进程列表。"""
    target = str(Path(str(profile_dir)).resolve())
    holders = []
    for pid, comm, cmdline in _iter_proc_cmdlines():
        if target not in cmdline:
            continue
        low = (comm or '').lower() + ' ' + cmdline.lower()
        holders.append({
            'pid': pid,
            'comm': comm,
            'browser_like': any(h in low for h in BROWSER_IMAGE_HINTS),
        })
    return holders


def reclaim(profile_dir: str, terminate: bool = False) -> dict:
    """检测（可选终止）占用该目录的进程。"""
    holders = find_holders(profile_dir)
    killed = []
    skipped = []
    if terminate:
        for item in holders:
            if not item.get('browser_like'):
                skipped.append(item)
                continue
            try:
                os.kill(item['pid'], 9)
                killed.append(item)
            except Exception:
                skipped.append(item)
    return {
        'profile': str(profile_dir),
        'holders': holders,
        'killed': killed,
        'skipped': skipped,
    }


def reclaim_stale(root: str, terminate: bool = False, limit: int = 0) -> dict:
    """扫描整个 profile 根目录，报告（并可选清理）被占用的 profile。"""
    base = Path(str(root or ''))
    if not base.is_dir():
        return {'ok': False, 'error': '目录不存在：%s' % root}
    profiles = [d for d in base.iterdir() if d.is_dir()]
    if limit and limit > 0:
        profiles = profiles[:limit]
    busy = []
    clean = 0
    for profile in profiles:
        holders = find_holders(str(profile))
        if holders:
            busy.append({'profile': profile.name, 'holders': holders})
            if terminate:
                reclaim(str(profile), terminate=True)
        else:
            clean += 1
    return {'ok': True, 'total': len(profiles), 'clean': clean, 'busy': busy}
