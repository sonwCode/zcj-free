#!/usr/bin/env python3
"""查看签名层（sentinel）版本与漂移状态。

用法：
    python tools/sentinel_info.py            # 本地指纹 + 线上漂移检测
    python tools/sentinel_info.py --local    # 只看本地
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
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    import config as cfg
    cfg.reload_all()
    from core.sentinel_provenance import check_drift, local_fingerprint

    local = local_fingerprint()
    data = {'local': local}
    if not args.local:
        data['drift'] = check_drift()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    sdk = local['sdk']
    runner = local['runner']
    print('SV 版本   :', local['sv'])
    print('sdk.js    : %s 字节  sha256=%s' % (sdk['bytes'], sdk['sha256'][:16]))
    print('runner.js : %s 字节  sha256=%s' % (runner['bytes'], runner['sha256'][:16]))
    if 'drift' in data:
        drift = data['drift']
        print('漂移检测   : %s（%s）' % (drift['status'], drift['detail']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
