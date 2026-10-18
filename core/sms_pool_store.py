# -*- coding: utf-8 -*-
"""低价号码池持久层：表迁移、原子领取、状态机与回收。

设计要点（SESSION_HANDOFF 第 35.11 节）：
  - 号码池状态独立于普通注册链，写入同一 SQLite 但使用独立表。
  - 领取必须在 SQLite 事务中原子完成，用条件更新 + 唯一索引防止两个 worker 抢同一号码。
  - 不使用进程内 set 做互斥，因为 worker 可能多进程或服务重启。
  - 日志与表内只保存脱敏号码和号码哈希，绝不保存 API key、完整号码或验证码。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from core import db

logger = logging.getLogger(__name__)

_MIGRATE_LOCK = threading.RLock()
_MIGRATED = False

# 号码池激活状态机
STATUS_RESERVED = "reserved"      # 已购买并锁定，未分配
STATUS_CLAIMED = "claimed"        # 已领取给某账号，任务尚未开始
STATUS_RUNNING = "running"        # 手机验证任务运行中
STATUS_WAITING_SMS = "waiting_sms"  # 已提交号码，等待短信
STATUS_COMPLETED = "completed"    # 验证成功
STATUS_RELEASED = "released"      # 主动释放
STATUS_EXPIRED = "expired"        # 本地/平台有效期到期
STATUS_FAILED = "failed"          # 失败并已释放
STATUS_ORPHANED = "orphaned"      # 状态无法与平台对账，禁止复用

ACTIVE_STATUSES = (STATUS_CLAIMED, STATUS_RUNNING, STATUS_WAITING_SMS)
HOLDING_STATUSES = (STATUS_RESERVED,) + ACTIVE_STATUSES

_TABLE_RESERVATIONS = "sms_pool_reservations"
_TABLE_EVENTS = "sms_pool_events"
_TABLE_REQUESTS = "sms_pool_requests"
_POLL_STATE_KEY = "sms_pool_poll_state"

# 手动补跑请求状态
REQUEST_PENDING = "pending"
REQUEST_CLAIMED = "claimed"
REQUEST_DONE = "done"
REQUEST_FAILED = "failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def phone_hash(phone: str) -> str:
    """只保存号码哈希，用于跨激活去重，不保存原始号码。"""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return ""
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()[:32]


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) <= 4:
        return digits or ""
    return "*" * max(len(digits) - 4, 2) + digits[-4:]


def ensure_tables() -> None:
    """幂等建表；可重复调用。"""
    global _MIGRATED
    with _MIGRATE_LOCK:
        if _MIGRATED:
            return
        conn = db._sqlite_conn()
        try:
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_RESERVATIONS} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL DEFAULT '',
                    activation_id TEXT NOT NULL DEFAULT '',
                    account_id INTEGER,
                    email TEXT NOT NULL DEFAULT '',
                    codex_job_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    profile_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '{STATUS_RESERVED}',
                    service TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    phone_mask TEXT NOT NULL DEFAULT '',
                    phone_hash TEXT NOT NULL DEFAULT '',
                    price_amount TEXT NOT NULL DEFAULT '',
                    price_currency TEXT NOT NULL DEFAULT '',
                    price_cny TEXT NOT NULL DEFAULT '',
                    fx_rate TEXT NOT NULL DEFAULT '',
                    fx_source TEXT NOT NULL DEFAULT '',
                    max_price_cny TEXT NOT NULL DEFAULT '',
                    failure_code TEXT NOT NULL DEFAULT '',
                    failure_reason TEXT NOT NULL DEFAULT '',
                    release_result TEXT NOT NULL DEFAULT '',
                    activation_metadata TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT NOT NULL DEFAULT '',
                    claimed_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS {_TABLE_EVENTS} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id INTEGER NOT NULL DEFAULT 0,
                    event TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS {_TABLE_REQUESTS} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL DEFAULT 0,
                    email TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            # 同一账号同一时刻只保留一条待执行请求。
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_pool_request_account "
                f"ON {_TABLE_REQUESTS}(account_id) WHERE status='pending'"
            )
            # 同一激活只允许一行，防止重复登记同一号码。
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_pool_activation "
                f"ON {_TABLE_RESERVATIONS}(provider, activation_id)"
            )
            # 一个账号同时只能占用一个号码池激活（account_id 非空时唯一）。
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_pool_account_active "
                f"ON {_TABLE_RESERVATIONS}(account_id) "
                f"WHERE account_id IS NOT NULL AND status IN "
                f"('{STATUS_CLAIMED}','{STATUS_RUNNING}','{STATUS_WAITING_SMS}')"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_sms_pool_status ON {_TABLE_RESERVATIONS}(status, id)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_sms_pool_created ON {_TABLE_RESERVATIONS}(created_at DESC)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_sms_pool_event_reservation "
                f"ON {_TABLE_EVENTS}(reservation_id, id DESC)"
            )
            conn.commit()
        finally:
            conn.close()
        _MIGRATED = True


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    raw = str(result.get("activation_metadata") or "").strip()
    if raw:
        try:
            result["activation_metadata"] = json.loads(raw)
        except (ValueError, TypeError):
            result["activation_metadata"] = {}
    else:
        result["activation_metadata"] = {}
    metadata = result["activation_metadata"] if isinstance(result["activation_metadata"], dict) else {}
    result["phone"] = str(metadata.get("phone_number") or metadata.get("phone") or result.get("phone_mask") or "")
    country = str(result.get("country") or "").strip()
    country_name = str(metadata.get("phone_country_name") or metadata.get("phone_region") or "").strip()
    result["country_display"] = f"{country}/{country_name}" if country and country_name else (country or country_name)
    return result


def _log_event(conn: sqlite3.Connection, reservation_id: int, event: str, detail: str = "") -> None:
    try:
        conn.execute(
            f"INSERT INTO {_TABLE_EVENTS}(reservation_id, event, detail, created_at) VALUES(?,?,?,?)",
            (int(reservation_id or 0), str(event or ""), str(detail or "")[:500], _utcnow()),
        )
    except sqlite3.Error:
        logger.debug("号码池事件写入失败", exc_info=True)


def register_reservation(
    *,
    provider: str,
    activation_id: str,
    phone: str,
    service: str = "",
    country: str = "",
    price_amount: str = "",
    price_currency: str = "",
    price_cny: str = "",
    fx_rate: str = "",
    fx_source: str = "",
    max_price_cny: str = "",
    expires_in_seconds: int = 0,
    metadata: dict | None = None,
) -> dict | None:
    """登记一个已购买的号码为 reserved。

    同一 (provider, activation_id) 重复登记时返回已存在记录，不新建。
    """
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        now = _utcnow()
        expires_at = ""
        if int(expires_in_seconds or 0) > 0:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in_seconds))
            ).isoformat(timespec="seconds")
        try:
            cursor = conn.execute(
                f"""INSERT INTO {_TABLE_RESERVATIONS}
                (provider, activation_id, status, service, country, phone_mask, phone_hash,
                 price_amount, price_currency, price_cny, fx_rate, fx_source, max_price_cny,
                 activation_metadata, created_at, updated_at, expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(provider or "").strip().lower(),
                    str(activation_id or "").strip(),
                    STATUS_RESERVED,
                    str(service or "").strip(),
                    str(country or "").strip(),
                    mask_phone(phone),
                    phone_hash(phone),
                    str(price_amount or "").strip(),
                    str(price_currency or "").strip().upper(),
                    str(price_cny or "").strip(),
                    str(fx_rate or "").strip(),
                    str(fx_source or "").strip(),
                    str(max_price_cny or "").strip(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                    expires_at,
                ),
            )
            reservation_id = int(cursor.lastrowid or 0)
            _log_event(conn, reservation_id, "reserved", f"provider={provider} country={country}")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = conn.execute(
                f"SELECT * FROM {_TABLE_RESERVATIONS} WHERE provider=? AND activation_id=? LIMIT 1",
                (str(provider or "").strip().lower(), str(activation_id or "").strip()),
            ).fetchone()
            logger.info(
                "[号码池] 激活已登记，跳过重复入库：provider=%s activation=%s",
                provider, activation_id,
            )
            return _row_to_dict(existing)
        return get_reservation(reservation_id)
    finally:
        conn.close()


def get_reservation(reservation_id: int) -> dict | None:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        row = conn.execute(
            f"SELECT * FROM {_TABLE_RESERVATIONS} WHERE id=? LIMIT 1", (int(reservation_id),)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_reservations(
    *,
    statuses: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
    account_id: int | None = None,
) -> list[dict]:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        sql = f"SELECT * FROM {_TABLE_RESERVATIONS}"
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(str(s) for s in statuses)
        if account_id is not None:
            clauses.append("account_id=?")
            params.append(int(account_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_reservations(statuses: list[str] | None = None) -> int:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        sql = f"SELECT COUNT(*) FROM {_TABLE_RESERVATIONS}"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(str(s) for s in statuses)
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def claim_for_account(reservation_id: int, account_id: int, email: str = "") -> dict | None:
    """原子把 reserved 号码领取给某账号。

    使用条件更新，只有仍处于 reserved 且未被其他账号占用时才成功。
    返回更新后的记录；竞争失败返回 None。
    """
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        now = _utcnow()
        lease = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(60, int(_lease_seconds())))
        ).isoformat(timespec="seconds")
        cursor = conn.execute(
            f"""UPDATE {_TABLE_RESERVATIONS}
                SET status=?, account_id=?, email=?, claimed_at=?, updated_at=?, lease_expires_at=?
                WHERE id=? AND status=? AND account_id IS NULL""",
            (STATUS_CLAIMED, int(account_id), str(email or ""), now, now, lease,
             int(reservation_id), STATUS_RESERVED),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            logger.info(
                "[号码池] 领取竞争失败或状态不符：reservation=%s account=%s",
                reservation_id, account_id,
            )
            return None
        _log_event(conn, int(reservation_id), "claimed", f"account_id={account_id}")
        conn.commit()
        return get_reservation(int(reservation_id))
    except sqlite3.IntegrityError:
        # account_id 唯一索引冲突：该账号已有号码池激活。
        conn.rollback()
        logger.info("[号码池] 账号已有进行中的号码池激活：account=%s", account_id)
        return None
    finally:
        conn.close()


def _lease_seconds() -> int:
    try:
        from config import sms_pool as pool_cfg
        return max(60, int(getattr(pool_cfg, "SMS_POOL_LEASE_SECONDS", 900) or 900))
    except Exception:
        return 900


def update_status(
    reservation_id: int,
    status: str,
    *,
    failure_code: str = "",
    failure_reason: str = "",
    release_result: str = "",
    codex_job_id: str | None = None,
    run_id: str | None = None,
    profile_name: str | None = None,
    terminal: bool = False,
) -> dict | None:
    """更新状态；terminal=True 时写入 finished_at 并清除租约。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        now = _utcnow()
        sets = ["status=?", "updated_at=?"]
        params: list[Any] = [str(status or ""), now]
        if failure_code:
            sets.append("failure_code=?")
            params.append(str(failure_code)[:120])
        if failure_reason:
            sets.append("failure_reason=?")
            params.append(str(failure_reason)[:800])
        if release_result:
            sets.append("release_result=?")
            params.append(str(release_result)[:200])
        if codex_job_id is not None:
            sets.append("codex_job_id=?")
            params.append(str(codex_job_id))
        if run_id is not None:
            sets.append("run_id=?")
            params.append(str(run_id))
        if profile_name is not None:
            sets.append("profile_name=?")
            params.append(str(profile_name))
        if terminal:
            sets.append("finished_at=?")
            params.append(now)
            sets.append("lease_expires_at=?")
            params.append("")
        params.append(int(reservation_id))
        cursor = conn.execute(
            f"UPDATE {_TABLE_RESERVATIONS} SET {', '.join(sets)} WHERE id=?", params
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        _log_event(conn, int(reservation_id), str(status or ""), failure_code or release_result)
        conn.commit()
        return get_reservation(int(reservation_id))
    finally:
        conn.close()


def touch_lease(reservation_id: int) -> None:
    """刷新租约，表明任务仍在推进。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        lease = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(60, int(_lease_seconds())))
        ).isoformat(timespec="seconds")
        conn.execute(
            f"UPDATE {_TABLE_RESERVATIONS} SET updated_at=?, lease_expires_at=? WHERE id=?",
            (_utcnow(), lease, int(reservation_id)),
        )
        conn.commit()
    finally:
        conn.close()


def find_expired_unclaimed() -> list[dict]:
    """返回已过平台有效期但仍未分配的号码。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"""SELECT * FROM {_TABLE_RESERVATIONS}
                WHERE status=? AND expires_at!='' AND expires_at<=?""",
            (STATUS_RESERVED, _utcnow()),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def find_stale_active() -> list[dict]:
    """返回租约过期、状态仍为运行中的号码，用于重启后对账。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = conn.execute(
            f"""SELECT * FROM {_TABLE_RESERVATIONS}
                WHERE status IN ({placeholders})
                  AND lease_expires_at!='' AND lease_expires_at<=?""",
            list(ACTIVE_STATUSES) + [_utcnow()],
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def active_account_ids() -> set[int]:
    """当前被号码池占用的账号 ID 集合，用于 eligible 计算。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        placeholders = ",".join("?" for _ in HOLDING_STATUSES)
        rows = conn.execute(
            f"""SELECT DISTINCT account_id FROM {_TABLE_RESERVATIONS}
                WHERE status IN ({placeholders}) AND account_id IS NOT NULL""",
            list(HOLDING_STATUSES),
        ).fetchall()
        return {int(r[0]) for r in rows if r[0] is not None}
    finally:
        conn.close()


def stats() -> dict:
    """号码池统计：数量 + 累计花费（仅展示，不作预算门槛）。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS c FROM {_TABLE_RESERVATIONS} GROUP BY status"
        ).fetchall()
        by_status = {str(r["status"]): int(r["c"]) for r in rows}
        total = sum(by_status.values())
        spent_rows = conn.execute(
            f"""SELECT price_cny FROM {_TABLE_RESERVATIONS}
                WHERE price_cny!='' AND status IN (?,?,?,?)""",
            (STATUS_RESERVED, STATUS_CLAIMED, STATUS_RUNNING, STATUS_WAITING_SMS),
        ).fetchall()
        spent = 0.0
        for r in spent_rows:
            try:
                spent += float(r["price_cny"])
            except (TypeError, ValueError):
                continue
        all_rows = conn.execute(
            f"SELECT price_cny FROM {_TABLE_RESERVATIONS} WHERE price_cny!='' AND status=?",
            (STATUS_COMPLETED,),
        ).fetchall()
        spent_all = 0.0
        for r in all_rows:
            try:
                spent_all += float(r["price_cny"])
            except (TypeError, ValueError):
                continue
        return {
            "total": total,
            "by_status": by_status,
            "held": sum(by_status.get(s, 0) for s in HOLDING_STATUSES),
            "unclaimed": by_status.get(STATUS_RESERVED, 0),
            "running": sum(by_status.get(s, 0) for s in ACTIVE_STATUSES),
            "completed": by_status.get(STATUS_COMPLETED, 0),
            "failed": by_status.get(STATUS_FAILED, 0),
            "expired": by_status.get(STATUS_EXPIRED, 0),
            "orphaned": by_status.get(STATUS_ORPHANED, 0),
            "spent_cny_active": round(spent, 4),
            "spent_cny_total": round(spent_all, 4),
        }
    finally:
        conn.close()


def recent_events(limit: int = 50) -> list[dict]:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE_EVENTS} ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_reservation_event_summaries(*, limit: int = 20, offset: int = 0) -> list[dict]:
    """Return one latest event per reservation, newest reservation activity first."""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"""SELECT event.*
                FROM {_TABLE_EVENTS} AS event
                JOIN (
                    SELECT reservation_id, MAX(id) AS latest_id
                    FROM {_TABLE_EVENTS}
                    WHERE reservation_id > 0
                    GROUP BY reservation_id
                ) AS latest ON latest.latest_id = event.id
                ORDER BY event.id DESC
                LIMIT ? OFFSET ?""",
            (max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_reservation_event_summaries() -> int:
    """Count reservations represented in the runtime-event list."""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        row = conn.execute(
            f"SELECT COUNT(DISTINCT reservation_id) FROM {_TABLE_EVENTS} WHERE reservation_id > 0"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def reservation_events(reservation_id: int, limit: int = 200) -> list[dict]:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE_EVENTS} WHERE reservation_id=? ORDER BY id DESC LIMIT ?",
            (int(reservation_id), max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_poll_state(state: dict[str, Any]) -> None:
    """Persist the worker heartbeat and per-provider polling snapshot."""
    ensure_tables()
    payload = json.dumps(dict(state or {}), ensure_ascii=False, separators=(",", ":"))
    conn = db._sqlite_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO storage_meta(key, value) VALUES(?, ?)",
            (_POLL_STATE_KEY, payload),
        )
        conn.commit()
    finally:
        conn.close()


def get_poll_state() -> dict:
    """Return the latest worker heartbeat, or an empty snapshot."""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        row = conn.execute(
            "SELECT value FROM storage_meta WHERE key=? LIMIT 1",
            (_POLL_STATE_KEY,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    try:
        value = json.loads(str(row["value"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def enqueue_request(account_id: int, email: str = "") -> dict | None:
    """登记一条"该账号优先补跑"请求；同账号已有 pending 时返回已存在记录。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        now = _utcnow()
        try:
            cursor = conn.execute(
                f"INSERT INTO {_TABLE_REQUESTS}(account_id, email, status, created_at, updated_at)"
                f" VALUES(?,?,?,?,?)",
                (int(account_id), str(email or ""), REQUEST_PENDING, now, now),
            )
            conn.commit()
            request_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute(
                f"SELECT * FROM {_TABLE_REQUESTS} WHERE account_id=? AND status=? LIMIT 1",
                (int(account_id), REQUEST_PENDING),
            ).fetchone()
            return dict(row) if row is not None else None
        row = conn.execute(
            f"SELECT * FROM {_TABLE_REQUESTS} WHERE id=? LIMIT 1", (request_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_pending_requests(limit: int = 20) -> list[dict]:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE_REQUESTS} WHERE status=? ORDER BY id ASC LIMIT ?",
            (REQUEST_PENDING, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_request(request_id: int, status: str) -> None:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        conn.execute(
            f"UPDATE {_TABLE_REQUESTS} SET status=?, updated_at=? WHERE id=?",
            (str(status or ""), _utcnow(), int(request_id)),
        )
        conn.commit()
    finally:
        conn.close()


def finish_request_for_account(account_id: int, status: str) -> None:
    """把该账号仍未完成的补跑请求收口（done/failed）。"""
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        conn.execute(
            f"UPDATE {_TABLE_REQUESTS} SET status=?, updated_at=? "
            f"WHERE account_id=? AND status IN (?,?)",
            (str(status or ""), _utcnow(), int(account_id),
             REQUEST_PENDING, REQUEST_CLAIMED),
        )
        conn.commit()
    finally:
        conn.close()


def pending_request_account_ids() -> set[int]:
    ensure_tables()
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            f"SELECT account_id FROM {_TABLE_REQUESTS} WHERE status=?", (REQUEST_PENDING,)
        ).fetchall()
        return {int(r[0]) for r in rows if r[0] is not None}
    finally:
        conn.close()


def _reset_for_tests() -> None:
    global _MIGRATED
    with _MIGRATE_LOCK:
        _MIGRATED = False
