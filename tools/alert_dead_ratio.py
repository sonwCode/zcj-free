#!/usr/bin/env python3
"""巡检后告警：算出死亡比例，超过阈值就推送（可放 cron）。

用法：
    python tools/alert_dead_ratio.py --dry-run
    python tools/alert_dead_ratio.py --threshold 0.3
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _counts() -> dict:
    from core import db

    rows = db.list_accounts(limit=5000, archived=False) or []
    total = len(rows)
    dead = 0
    for acc in rows:
        status = str(acc.get('live_check_status') or '').lower()
        if status in ('deactivated', 'revoked', 'dead'):
            dead += 1
    return {'total': total, 'dead': dead}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=0.3)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--min-interval', type=float, default=1800.0)
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core.alerting import dead_ratio_exceeded, send, should_send

    counts = _counts()
    ratio = (counts['dead'] / counts['total']) if counts['total'] else 0.0
    print('样本 %d，已废 %d，比例 %.1f%%（阈值 %.0f%%）' % (
        counts['total'], counts['dead'], ratio * 100, args.threshold * 100))
    if not dead_ratio_exceeded(counts['total'], counts['dead'], args.threshold):
        print('未超阈值，不告警')
        return 0
    text = '【账号巡检】已废 %d / %d（%.1f%%），超过阈值 %.0f%%' % (
        counts['dead'], counts['total'], ratio * 100, args.threshold * 100)
    if args.dry_run:
        print('[dry-run] 将发送：', text)
        return 0
    try:
        from config import alerting as acfg

        webhook = str(getattr(acfg, 'ALERT_WEBHOOK', '') or '')
        channel = str(getattr(acfg, 'ALERT_CHANNEL', 'wecom') or 'wecom')
    except Exception:
        webhook, channel = '', 'wecom'
    if not webhook:
        print('未配置 ALERT_WEBHOOK，仅打印：', text)
        return 0
    if not should_send('dead_ratio', args.min_interval):
        print('节流中，跳过本次告警')
        return 0
    result = send(text, webhook, channel)
    print('发送结果：', json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
