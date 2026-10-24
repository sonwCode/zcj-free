#!/usr/bin/env python3
"""给邮箱池做分裂（子地址）：user@x.com -> user+<6位随机>@x.com。

用法：
    python tools/split_email_pool.py --source all --count 3          # 预演
    python tools/split_email_pool.py --source imap --count 5 --apply

各池规则：
  imap      : 别名行 imap_username = 主邮箱（登录用它），密码/服务器沿用
  generic_api: 别名行沿用同一 code_url
  outlook   : 别名行沿用同一 refresh_token / client_id / password
  cloudflare_domain: 跳过（每个地址本身是独立临时邮箱）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _imap_records(rows, count, limit):
    from core.email_splitter import split_email

    records = []
    for row in rows[:limit]:
        parent = str(row.get("email") or "").strip()
        if not parent:
            continue
        for alias in split_email(parent, count):
            records.append({
                "email": alias,
                "imap_password": row.get("imap_password") or "",
                "imap_server": row.get("imap_server") or "",
                "imap_port": row.get("imap_port") or 993,
                "imap_ssl": row.get("imap_ssl", True),
                # 关键：登录名要**继承父行的 imap_username**，不能直接用地址。
                # iCloud 常见结构是 地址=别名(如 xxx@icloud.com)、登录名=Apple ID，
                # 用地址去登录会 AUTHENTICATIONFAILED。
                "imap_username": str(row.get("imap_username") or row.get("username") or parent),
            })
    return records


def _generic_records(rows, count, limit):
    from core.email_splitter import split_email

    records = []
    for row in rows[:limit]:
        parent = str(row.get("email") or "").strip()
        if not parent:
            continue
        for alias in split_email(parent, count):
            records.append({"email": alias, "code_url": row.get("code_url") or ""})
    return records


def _outlook_records(rows, count, limit):
    from core.email_splitter import split_email

    records = []
    for row in rows[:limit]:
        parent = str(row.get("email") or "").strip()
        if not parent:
            continue
        for alias in split_email(parent, count):
            records.append({
                "email": alias,
                "password": row.get("password") or "",
                "client_id": row.get("client_id") or "",
                "refresh_token": row.get("refresh_token") or "",
            })
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all",
                    choices=["all", "imap", "generic_api", "outlook"])
    ap.add_argument("--count", type=int, default=3, help="每个邮箱派生几个子地址")
    ap.add_argument("--limit", type=int, default=50, help="最多处理多少个父邮箱")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import config as cfg
    cfg.reload_all()
    from core import db

    sources = ["imap", "generic_api", "outlook"] if args.source == "all" else [args.source]
    total_new = 0
    for src in sources:
        if src == "imap":
            rows = db.list_imap_email_pool() or []
            records = _imap_records(rows, args.count, args.limit)
            writer = db.import_imap_emails
        elif src == "generic_api":
            rows = db.list_generic_api_email_pool() or []
            records = _generic_records(rows, args.count, args.limit)
            writer = db.import_generic_api_emails
        else:
            rows = db.list_outlook_accounts() if hasattr(db, "list_outlook_accounts") else []
            records = _outlook_records(rows, args.count, args.limit)
            writer = db.import_outlook_accounts
        print("[%s] 父邮箱 %d 个 → 生成别名 %d 条" % (src, len(rows[:args.limit]), len(records)))
        if not records:
            continue
        for rec in records[:3]:
            print("   样例:", rec.get("email"), "| 登录名:", rec.get("imap_username") or "-")
        if args.apply:
            inserted, skipped = writer(records)
            total_new += inserted
            print("   已入库 %d，跳过 %d" % (inserted, skipped))
    if not args.apply:
        print("\n[预演] 加 --apply 才会写库")
    else:
        print("\n总计新增:", total_new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())