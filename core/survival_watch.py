# -*- coding: utf-8 -*-
"""账号存活追踪：记录每次探活结论，回答"注册后几天出试用"。

背景（用户观察）：协议注册出来的号要"能活下来过几天才会出试用"，
所以需要按账号记录生命周期，而不是只看瞬时 alive/revoked。

设计：
  * 每次探活写一条 JSONL（data/survival_log.jsonl）：
      {ts, account_id, email, verdict, detail, at_days}
  * at_days = 距注册天数；
  * summary() 汇总每个账号：首次/最近结论、存活天数、是否出现过 alive；
  * 只追加写，不覆盖历史。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_PATH = Path("/opt/turb-gpt-register/data/survival_log.jsonl")


def _ts(value=None) -> float:
    return float(time.time() if value is None else value)


def record(account_id: int, email: str, verdict: str, detail: str = "",
           created_at: str = "", now: float | None = None,
           path: Path | None = None) -> dict:
    """追加一条探活记录，返回写入的条目。"""
    stamp = _ts(now)
    days = None
    if created_at:
        try:
            from datetime import datetime

            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            days = round((stamp - created.timestamp()) / 86400.0, 2)
        except Exception:
            days = None
    entry = {
        "ts": stamp,
        "account_id": int(account_id or 0),
        "email": str(email or ""),
        "verdict": str(verdict or ""),
        "detail": str(detail or "")[:200],
        "at_days": days,
    }
    target = Path(path or LOG_PATH)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("[存活追踪] 写入失败")
    return entry


def load(path: Path | None = None) -> list:
    target = Path(path or LOG_PATH)
    out = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def summary(path: Path | None = None) -> dict:
    """按账号汇总：出现过的结论、最近结论、存活天数。"""
    entries = load(path)
    per = {}
    for e in entries:
        key = e.get("email") or str(e.get("account_id"))
        item = per.setdefault(key, {
            "account_id": e.get("account_id"),
            "email": e.get("email"),
            "checks": 0,
            "first_ts": e.get("ts"),
            "last_ts": e.get("ts"),
            "verdicts": {},
            "last_verdict": e.get("verdict"),
            "at_days": e.get("at_days"),
        })
        item["checks"] += 1
        item["last_ts"] = e.get("ts")
        item["last_verdict"] = e.get("verdict")
        item["at_days"] = e.get("at_days")
        v = str(e.get("verdict") or "")
        item["verdicts"][v] = item["verdicts"].get(v, 0) + 1
    return {
        "total_entries": len(entries),
        "accounts": len(per),
        "alive_once": sum(1 for i in per.values() if i["verdicts"].get("alive")),
        "revoked_only": sum(1 for i in per.values()
                            if not i["verdicts"].get("alive") and i["verdicts"].get("revoked")),
        "items": per,
    }