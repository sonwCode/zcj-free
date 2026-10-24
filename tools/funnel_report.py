#!/usr/bin/env python3
"""注册漏斗报表：按状态与失败类别统计，回答"卡在哪一步"。

用法：
    python tools/funnel_report.py            # 最近 200 条
    python tools/funnel_report.py --limit 0  # 全部
    python tools/funnel_report.py --json
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / 'turb.sqlite3'


def _rows(limit: int) -> list:
    con = sqlite3.connect(str(DB))
    try:
        sql = 'select id, status, payload from registration_jobs order by id desc'
        if limit and limit > 0:
            sql += ' limit %d' % int(limit)
        return con.execute(sql).fetchall()
    finally:
        con.close()


def build(limit: int = 200) -> dict:
    from core.failure_registry import classify_error

    status_counts: dict = {}
    sub_status: dict = {}
    failure_classes: dict = {}
    for _job_id, status, payload in _rows(limit):
        key = str(status or 'unknown')
        status_counts[key] = status_counts.get(key, 0) + 1
        try:
            data = json.loads(payload or '{}')
        except Exception:
            data = {}
        if key in ('partial_success', 'failed'):
            acc = str(data.get('account_status') or '')
            codex = str(data.get('codex_status') or '')
            if acc:
                sub_status['account_status:' + acc] = sub_status.get('account_status:' + acc, 0) + 1
            if codex:
                sub_status['codex_status:' + codex] = sub_status.get('codex_status:' + codex, 0) + 1
            # 报表口径：优先用结构化 error_code，其次才落到对错误的文本分类。
            # 否则 runtimeerror 这种"只是异常类型名"的码会吞掉真实原因。
            code = str(data.get('error_code') or '').strip().lower()
            message = str(data.get('error_message') or '')
            result = data.get('result') if isinstance(data.get('result'), dict) else {}
            fallback = result.get('error') or data.get('error') or ''
            if code and code not in ('runtimeerror', 'exception', 'error'):
                key_cls = code
            elif message or fallback:
                key_cls = classify_error(message or fallback)
            else:
                key_cls = ''
            if key_cls:
                failure_classes[key_cls] = failure_classes.get(key_cls, 0) + 1
    total = sum(status_counts.values())
    return {
        'total': total,
        'status': status_counts,
        'sub_status': sub_status,
        'failure_classes': failure_classes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    report = build(args.limit)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    total = report['total'] or 1
    print('窗口内作业数: %d' % report['total'])
    print('')
    print('%-18s %6s %7s' % ('状态', '数量', '占比'))
    for name, count in sorted(report['status'].items(), key=lambda x: -x[1]):
        print('%-18s %6d %6.1f%%' % (name, count, count * 100.0 / total))
    if report['sub_status']:
        print('')
        print('部分成功/失败 的细分：')
        for name, count in sorted(report['sub_status'].items(), key=lambda x: -x[1])[:10]:
            print('  %-28s %5d' % (name, count))
    if report['failure_classes']:
        print('')
        print('失败分类：')
        for name, count in sorted(report['failure_classes'].items(), key=lambda x: -x[1]):
            print('  %-18s %5d' % (name, count))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
