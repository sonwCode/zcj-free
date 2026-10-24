#!/usr/bin/env python3
"""存活报表：回答"注册后几天出试用"。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def main() -> int:
    import config as cfg
    cfg.reload_all()
    from core import survival_watch
    s = survival_watch.summary()
    print("探活记录 %d 条 | 账号 %d 个 | 曾 alive %d | 只 revoke %d"
          % (s["total_entries"], s["accounts"], s["alive_once"], s["revoked_only"]))
    items = sorted(s["items"].values(), key=lambda x: x.get("at_days") or 0)
    print("\n账号存活明细（按注册天数）:")
    for it in items[:40]:
        print("  %-32s 天数 %-6s 次数 %-3s 最近 %-9s %s"
              % (str(it["email"])[:32], it["at_days"], it["checks"],
                 it["last_verdict"], it["verdicts"]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())