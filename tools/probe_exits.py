#!/usr/bin/env python3
"""探测代理池的 OpenAI 边缘可用性（只读，不改任何状态）。

用法：
    python tools/probe_exits.py              # 探测整池（3 个边缘点）
    python tools/probe_exits.py --quick      # 只探入口页
    python tools/probe_exits.py --json       # 输出 JSON

判定：clean / blocked / degraded / dead
"""
from __future__ import annotations
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
EDGES = (
    ('chatgpt', 'https://chatgpt.com/auth/login'),
    ('auth', 'https://auth.openai.com/log-in'),
    ('otp', 'https://auth.openai.com/api/accounts/email-otp/send'),
)


def _redact(proxy: str) -> str:
    text = str(proxy or '')
    if 'sid-' in text:
        return 'sid-' + text.split('sid-')[-1].split('-t-')[0][:8]
    if '@' in text:
        return '***@' + text.split('@', 1)[1]
    return text[:24]


def _probe(task: tuple[str, tuple[str, str]]) -> dict:
    proxy, (label, url) = task
    import requests

    try:
        resp = requests.get(url, proxies={'http': proxy, 'https': proxy}, timeout=15,
                            allow_redirects=False, headers={'User-Agent': UA,
                                                           'Accept': 'text/html,application/xhtml+xml'})
        body = (resp.text or '')[:1200].lower()
        code = int(resp.status_code or 0)
        cf = bool(code == 403 or 'just a moment' in body or 'cf-chl' in body)
        verdict = 'clean' if (code in (200, 204, 302, 401, 429) and not cf) else ('blocked' if cf else 'degraded')
        return {'proxy': _redact(proxy), 'edge': label, 'status': verdict, 'http': code}
    except Exception as exc:
        return {'proxy': _redact(proxy), 'edge': label, 'status': 'dead',
                'error': type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from config.proxy import PROXY_POOL
    if not PROXY_POOL:
        print('代理池为空')
        return 1
    edges = EDGES[:1] if args.quick else EDGES
    tasks = [(p, e) for p in PROXY_POOL for e in edges]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(_probe, tasks))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        from collections import defaultdict
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[row['proxy']].append(row)
        for proxy, items in grouped.items():
            marks = ' '.join('%s=%s' % (i['edge'], i.get('http') or i.get('error')) for i in items)
            worst = 'blocked' if any(i['status'] == 'blocked' for i in items) else (
                'dead' if any(i['status'] == 'dead' for i in items) else (
                'degraded' if any(i['status'] == 'degraded' for i in items) else 'clean'))
            print('%-14s %-9s %s' % (proxy, worst, marks))
        counts = {}
        for row in rows:
            counts[row['status']] = counts.get(row['status'], 0) + 1
        print()
        print('汇总:', counts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
