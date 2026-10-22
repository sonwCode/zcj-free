# -*- coding: utf-8 -*-
"""流量报告：从注册日志里汇总每次运行的收发字节，用于省流量改动前后对比。

用法：python tests/traffic_report.py [天数]
"""
import re
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path("/opt/turb-gpt-register/注册日志")
PATTERN = re.compile(r"合计 ([0-9.]+) KiB.*?HTTP请求 (\d+)")


def collect(lines):
    rows = []
    for line in lines:
        m = PATTERN.search(line)
        if m:
            rows.append((float(m.group(1)), int(m.group(2))))
    return rows


def main(days: int = 3) -> int:
    cmd = ["grep", "-rh", "注册浏览器网络流量", str(LOG_DIR)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    rows = collect(out.splitlines())
    if not rows:
        print("没找到流量记录")
        return 1
    totals = [r[0] for r in rows]
    reqs = [r[1] for r in rows]
    totals.sort()
    n = len(totals)
    mid = totals[n // 2]
    print("样本数: %d" % n)
    print("合计 KiB: 中位数 %.1f | 平均 %.1f | 最小 %.1f | 最大 %.1f" % (
        mid, sum(totals) / n, totals[0], totals[-1]))
    print("请求数中位数: %d" % sorted(reqs)[len(reqs) // 2])
    print("折算 MB（中位数）: %.1f MB/次" % (mid / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
