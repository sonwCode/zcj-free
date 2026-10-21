# -*- coding: utf-8 -*-
"""低价号码池后台 worker。

职责（SESSION_HANDOFF 第 35.11 节）：
  - 每 SMS_POOL_POLL_INTERVAL 秒轮询一次，不运行在 Flask 请求线程。
  - 需求驱动采购：eligible 账号为 0 时不购买任何号码。
  - 并发把已锁定号码分配给合格账号，上限 min(已领取数, 目标账号数, SMS_POOL_WORKERS)。
  - 回收过期未分配号码，把租约过期的运行中号码标记 orphaned。
  - 用 SQLite 单例租约保证同一时刻只有一个 poller。

本模块必须由独立的 systemd unit 运行，不允许由 WebUI 进程启动。
原因：完整号码只保存在进程内的激活状态里，采购与执行必须同进程，
WebUI 另起进程轮询会导致"号码买到了但任务拿不到号码"。
start() 会用 SMS_POOL_WORKER_PROCESS=1 做进程角色校验。
"""
from __future__ import annotations

import logging
import os
import random
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from core import db
from core import sms_pool_service as service
from core import sms_pool_store as store
from core import sms_pool_log as pool_log

logger = logging.getLogger(__name__)

_LEADER_KEY = "sms_pool_leader"
_LEADER_TTL_SECONDS = 180
_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.RLock()
_ACTIVE_TASKS: dict[int, threading.Thread] = {}
_ACTIVE_LOCK = threading.RLock()
_ACCOUNT_RANDOM = random.SystemRandom()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _write_poll_state(*, status: str, providers: list[str], current_provider: str = "", provider_state: dict | None = None, next_poll_at: str = "", last_result: str = "", error: str = "") -> None:
    now = _utcnow()
    try:
        previous = store.get_poll_state()
        store.save_poll_state({
            "status": str(status or "unknown"),
            "owner": _owner_id(),
            "heartbeat_at": now,
            "last_poll_at": now if status in {"polling", "paused", "idle", "error"} else str(previous.get("last_poll_at") or ""),
            "next_poll_at": str(next_poll_at or previous.get("next_poll_at") or ""),
            "current_provider": str(current_provider or previous.get("current_provider") or ""),
            "providers": list(providers or []),
            "provider_state": dict(provider_state if provider_state is not None else previous.get("provider_state") or {}),
            "last_result": str(last_result or "")[:240],
            "error": str(error or "")[:240],
        })
    except Exception:
        logger.debug("[号码池] 写入轮询状态失败", exc_info=True)


def _try_become_leader() -> bool:
    """用 SQLite 单例租约保证只有一个 poller。

    抢占必须原子：先读后无条件写会让两个进程同时认为自己拿到 leader。
    这里读取当前值后用 "WHERE value=?" 做比较交换；期间被其他进程改写则
    rowcount 为 0，本次抢占明确失败，不会出现双 leader。
    """
    store.ensure_tables()
    conn = db._sqlite_conn()
    try:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=_LEADER_TTL_SECONDS)).isoformat(timespec="seconds")
        owner = _owner_id()
        desired = f"{owner}|{expires}"
        row = conn.execute(
            "SELECT value FROM storage_meta WHERE key=? LIMIT 1", (_LEADER_KEY,)
        ).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO storage_meta(key, value) VALUES(?,?)",
                (_LEADER_KEY, desired),
            )
            conn.commit()
            if cursor.rowcount == 1:
                return True
            row = conn.execute(
                "SELECT value FROM storage_meta WHERE key=? LIMIT 1", (_LEADER_KEY,)
            ).fetchone()
            if row is None:
                return False
        previous = str(row["value"] or "")
        current_owner, _, current_expires = previous.partition("|")
        try:
            parsed = datetime.fromisoformat(current_expires)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = None
        if not (current_owner == owner or parsed is None or parsed <= now):
            conn.rollback()
            return False
        cursor = conn.execute(
            "UPDATE storage_meta SET value=? WHERE key=? AND value=?",
            (desired, _LEADER_KEY, previous),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        logger.debug("号码池 leader 租约检查失败", exc_info=True)
        return False
    finally:
        conn.close()


def _release_leadership() -> None:
    store.ensure_tables()
    conn = db._sqlite_conn()
    try:
        conn.execute(
            "DELETE FROM storage_meta WHERE key=? AND value LIKE ?",
            (_LEADER_KEY, f"{_owner_id()}|%"),
        )
        conn.commit()
    except Exception:
        logger.debug("号码池 leader 释放失败", exc_info=True)
    finally:
        conn.close()


def _reap_finished_tasks() -> None:
    with _ACTIVE_LOCK:
        finished = [rid for rid, t in _ACTIVE_TASKS.items() if not t.is_alive()]
        for rid in finished:
            _ACTIVE_TASKS.pop(rid, None)


def _dispatch_task(reservation: dict, account: dict) -> bool:
    """为一个号码 + 一个账号启动独立的手机验证任务。"""
    from core import sms_pool_task

    reservation_id = int(reservation["id"])
    with _ACTIVE_LOCK:
        existing = _ACTIVE_TASKS.get(reservation_id)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=sms_pool_task.run_pool_task,
            kwargs={"reservation_id": reservation_id, "account": account},
            name=f"sms-pool-task-{reservation_id}",
            daemon=True,
        )
        _ACTIVE_TASKS[reservation_id] = thread
        thread.start()
        return True


def run_once() -> dict:
    """执行一轮：对账 → 回收 → 采购 → 并发分配。"""
    cfg = service._pool_cfg()
    configured = service.configured_pool_providers()
    # 有足够历史样本时优先成功率更高的平台；样本不足时保留配置链顺序。
    # 价格/库存仍由各平台自己的 acquire_one 决定，不会绕过人民币上限。
    try:
        from core import sms_provider

        service_code = str(getattr(cfg, "SMS_POOL_SERVICE", "") or "").strip()
        if not service_code:
            service_code = str(getattr(sms_provider._cfg, "SMS_SERVICE", "") or "").strip()
        configured = sms_provider._quality_rank_providers(configured, service_code)
    except Exception:
        logger.debug("[号码池] 质量排序不可用，保留平台配置顺序", exc_info=True)
    provider_state = {provider: {"status": "waiting", "last_result": ""} for provider in configured}
    _write_poll_state(status="polling", providers=configured, provider_state=provider_state, last_result="开始轮询")
    live_checks = 0
    if not bool(getattr(cfg, "SMS_POOL_ENABLED", False)):
        _write_poll_state(status="disabled", providers=configured, provider_state=provider_state, last_result="号码池未启用")
        return {"skipped": "disabled", "live_checks": live_checks}

    # 1) 对账：过期释放 + orphaned
    reconcile_result = service.reconcile()

    # 2) 代理/网络故障时暂停新查活、采购和分配。已有任务仍按原流程收口，
    # 网络失败会把号码放回 reserved，不会调用供应商取消接口。
    from core import sms_pool_task
    circuit = sms_pool_task.network_circuit_state()
    if circuit.get("open"):
        remaining = int(circuit.get("remaining") or 0)
        last_error = str(circuit.get("last_error") or "网络连接失败")
        provider_state = {
            provider: {
                "status": "circuit_open",
                "last_result": f"网络熔断，约 {remaining}s 后重试",
            }
            for provider in configured
        }
        logger.warning(
            "[号码池] 网络熔断中，跳过查活、采购和分配：remaining=%ss streak=%s error=%s",
            remaining, circuit.get("failure_streak"), last_error,
        )
        _write_poll_state(
            status="paused",
            providers=configured,
            provider_state=provider_state,
            last_result=f"代理/网络异常，暂停采购和分配（约 {remaining}s）",
            error=last_error,
        )
        return {
            "reconcile": reconcile_result,
            "purchased": 0,
            "dispatched": 0,
            "live_checks": 0,
            "network_circuit": circuit,
        }

    live_checks = service.enqueue_scheduled_live_checks()

    # 3) 采购（暂停时只停止新抢号，不中断等待短信的激活）
    purchased = 0
    paused = bool(getattr(cfg, "SMS_POOL_PAUSED", False))
    providers = configured
    if not paused and providers:
        capacity = service.purchase_capacity()
        need = int(capacity.get("capacity") or 0)
        if need > 0:
            logger.info(
                "[号码池] 采购需求：capacity=%s eligible=%s unclaimed=%s active=%s",
                need, capacity.get("eligible"), capacity.get("unclaimed"), capacity.get("active"),
            )
            # Try each configured provider until the prewarm capacity is met.
            # A provider with no stock must not prevent later providers from
            # being queried in the same polling round.
            for provider in providers:
                if purchased >= need:
                    break
                provider_state[provider] = {"status": "requesting", "last_result": "请求库存/低价号码"}
                _write_poll_state(status="polling", providers=providers, current_provider=provider, provider_state=provider_state, last_result=f"正在请求 {provider}")
                try:
                    reservation = service.acquire_one(provider)
                except Exception as exc:
                    provider_state[provider] = {"status": "error", "last_result": f"{type(exc).__name__}: {exc}"[:240]}
                    _write_poll_state(status="error", providers=providers, current_provider=provider, provider_state=provider_state, last_result=f"{provider} 请求异常", error=str(exc))
                    reservation = None
                if reservation is not None:
                    provider_state[provider] = {"status": "success", "last_result": f"已锁定号码 #{reservation.get('id')}"}
                else:
                    provider_state[provider] = {"status": "no_match", "last_result": "本轮未锁定号码"}
                _write_poll_state(status="polling", providers=providers, current_provider=provider, provider_state=provider_state, last_result=f"{provider} 本轮完成")
                if reservation is None:
                    continue
                purchased += 1
        elif int(capacity.get("eligible") or 0) <= 0:
            logger.info("[号码池] 无合格账号，跳过采购")
            _write_poll_state(status="idle", providers=providers, provider_state=provider_state, last_result="无合格账号，跳过采购")
        else:
            _write_poll_state(status="idle", providers=providers, provider_state=provider_state, last_result="预热池已满，本轮不采购")
    elif paused:
        logger.info("[号码池] 已暂停，跳过新采购")
        provider_state = {provider: {"status": "paused", "last_result": "已暂停，未请求平台"} for provider in providers}
        _write_poll_state(status="paused", providers=providers, provider_state=provider_state, last_result="已暂停，未请求平台")
    elif not providers:
        _write_poll_state(status="error", providers=[], provider_state={}, last_result="没有已配置平台", error="no_configured_provider")

    # 4) 并发分配
    dispatched = _dispatch_available()

    return {
        "reconcile": reconcile_result,
        "purchased": purchased,
        "dispatched": dispatched,
        "live_checks": live_checks,
    }


def _dispatch_available() -> int:
    """把已锁定号码分配给合格账号，受 worker 上限约束。"""
    cfg = service._pool_cfg()
    max_workers = max(1, int(getattr(cfg, "SMS_POOL_WORKERS", 2) or 2))

    _reap_finished_tasks()
    with _ACTIVE_LOCK:
        running = len(_ACTIVE_TASKS)
    if running >= max_workers:
        return 0

    scan = service.scan_eligible_accounts()
    eligible_list = list(scan.get("eligible") or [])
    if not eligible_list:
        return 0

    unclaimed = store.list_reservations(statuses=[store.STATUS_RESERVED], limit=50)
    if not unclaimed:
        return 0

    # 未知或过期的活性先进入已有异步查活队列；只有确认账号仍然可用后才能分配号码。
    eligible_by_id = {
        int(item["id"]): item
        for item in eligible_list
        if service.is_live_check_ready(item)
    }
    check_slots = min(len(unclaimed), max_workers - running)
    checks_needed = max(0, check_slots - len(eligible_by_id))
    if checks_needed:
        service.enqueue_live_checks(
            limit=checks_needed,
            trigger="sms_pool_preflight",
        )
    if not eligible_by_id:
        return 0

    # 兼容旧版已排队的账号优先级，但不再要求手动入队；剩余号码自动补给其他候选。
    ordered: list[dict] = []
    request_by_account: dict[int, int] = {}
    for req in store.list_pending_requests(limit=50):
        account_id = int(req.get("account_id") or 0)
        if account_id in eligible_by_id and account_id not in request_by_account:
            request_by_account[account_id] = int(req["id"])
            ordered.append(eligible_by_id[account_id])
    selected = set(request_by_account)
    candidates = [
        item for account_id, item in eligible_by_id.items()
        if account_id not in selected
    ]

    # 不再按账号 ID 固定从前往后取。没有号码池历史的账号优先；有历史的
    # 按最近一次分配时间从旧到新轮转，同一层再用系统随机打散，避免补跑
    # 失败后总是回到同几个低 ID free 账号。
    recent_rank: dict[int, int] = {}
    try:
        for rank, reservation in enumerate(store.list_reservations(limit=500)):
            account_id = int(reservation.get("account_id") or 0)
            if account_id and account_id not in recent_rank:
                recent_rank[account_id] = rank
    except Exception:
        logger.debug("[号码池] 读取账号分配历史失败，使用随机顺序", exc_info=True)
    _ACCOUNT_RANDOM.shuffle(candidates)
    ordered.extend(
        sorted(
            candidates,
            key=lambda item: (
                0 if int(item["id"]) not in recent_rank else 1,
                -recent_rank.get(int(item["id"]), 0),
            ),
        )
    )

    # 并发上限 = min(已领取号码数, 可分配账号数, workers)
    limit = min(len(unclaimed), len(ordered), max_workers - running)
    if limit <= 0:
        return 0

    dispatched = 0
    for reservation, account in zip(unclaimed, ordered):
        if dispatched >= limit:
            break
        reservation_id = int(reservation["id"])
        account_id = int(account["id"])
        claimed = store.claim_for_account(reservation_id, account_id, str(account.get("email") or ""))
        if claimed is None:
            continue
        request_id = request_by_account.get(account_id)
        if request_id:
            store.mark_request(request_id, store.REQUEST_CLAIMED)
        if _dispatch_task(claimed, account):
            dispatched += 1
            logger.info(
                "[号码池] 已自动分配：reservation=%s account=%s email=%s 优先请求=%s",
                reservation_id, account_id, account.get("email"), "yes" if request_id else "no",
            )
    return dispatched


def _release_active_on_shutdown() -> int:
    """进程退出前把本进程持有的号码收口。

    不做这一步，心跳随进程消失，15 分钟后对账会把这批号码判成 orphaned
    （failure_code=lease_expired），既看不出原因又占着已付费号码。
    """
    released = 0
    try:
        active = store.list_reservations(statuses=list(store.ACTIVE_STATUSES), limit=200)
    except Exception:
        logger.debug("[号码池] 停机收口读取活跃记录失败", exc_info=True)
        return 0
    if not active:
        return 0
    from core import sms_provider

    for row in active:
        reservation_id = int(row.get("id") or 0)
        activation_id = str(row.get("activation_id") or "").strip()
        email = str(row.get("email") or "")
        if activation_id:
            try:
                # 号码还没被手机验证消耗，停机时直接释放，避免继续占额度。
                sms_provider.cancel(activation_id, background=False)
            except Exception:
                logger.debug("[号码池] 停机释放号码失败：%s", activation_id, exc_info=True)
        try:
            store.update_status(
                reservation_id,
                store.STATUS_RELEASED,
                failure_code="worker_shutdown",
                failure_reason="worker 进程停止，号码已释放",
                terminal=True,
            )
            pool_log.append_task(reservation_id, email, "[停止] worker 退出，号码已释放")
            released += 1
        except Exception:
            logger.debug("[号码池] 停机收口写状态失败：%s", reservation_id, exc_info=True)
    logger.warning("[号码池] 停机收口完成：释放 %s 个在途号码", released)
    return released


def loop() -> None:
    """worker 主循环。"""
    logger.info("[号码池] worker 启动：owner=%s", _owner_id())
    while not _STOP_EVENT.is_set():
        # WebUI 改配置后需要在本进程生效：这里每轮热加载一次配置模块。
        try:
            import config as _config_pkg
            _config_pkg.reload_all()
        except Exception:
            logger.debug("[号码池] 配置热加载失败", exc_info=True)
        try:
            cfg = service._pool_cfg()
            interval = max(30, int(getattr(cfg, "SMS_POOL_POLL_INTERVAL", 120) or 120))
        except Exception:
            interval = 120
        try:
            next_poll = (datetime.now(timezone.utc) + timedelta(seconds=interval)).isoformat(timespec="seconds")
            try:
                providers = service.configured_pool_providers()
                current = store.get_poll_state().get("current_provider", "")
                _write_poll_state(status="waiting", providers=providers, current_provider=current, next_poll_at=next_poll, last_result="等待下一轮")
            except Exception:
                pass
            if _try_become_leader():
                result = run_once()
                if result.get("skipped") != "disabled":
                    logger.debug("[号码池] 本轮结果：%s", result)
                    try:
                        pool_log.append_worker("[轮询] " + str(result)[:400])
                    except Exception:
                        logger.debug("号码池轮询日志写入失败", exc_info=True)
        except Exception:
            logger.exception("[号码池] worker 轮询异常")
        _STOP_EVENT.wait(interval)
    try:
        _release_active_on_shutdown()
    except Exception:
        logger.exception("[号码池] 停机收口异常")
    _release_leadership()
    logger.info("[号码池] worker 已停止")


def start(background: bool = True, *, force: bool = False) -> dict:
    """启动 worker 线程（幂等）。

    只允许专用号码池 worker 进程启动轮询：完整号码只存在于进程内激活状态，
    采购、分配、执行必须在同一进程，否则任务拿不到号码。需要环境变量
    SMS_POOL_WORKER_PROCESS=1，或者显式传 force=True（仅用于测试）。
    """
    role = str(os.environ.get("SMS_POOL_WORKER_PROCESS", "")).strip().lower()
    if not force and role not in {"1", "true", "yes", "on"}:
        logger.warning(
            "[号码池] 当前进程未声明为号码池 worker，拒绝启动轮询线程：pid=%s", os.getpid(),
        )
        return {
            "ok": False,
            "message": "当前进程不是号码池 worker（需要 SMS_POOL_WORKER_PROCESS=1）",
            "running": False,
        }
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return {"ok": True, "message": "worker 已在运行", "running": True}
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(target=loop, name="sms-pool-worker", daemon=True)
        _THREAD.start()
    if background:
        return {"ok": True, "message": "worker 已启动", "running": True}
    _THREAD.join()
    return {"ok": True, "message": "worker 已结束", "running": False}


def stop(timeout: float = 5.0) -> dict:
    """请求停止 worker。"""
    global _THREAD
    _STOP_EVENT.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(0.0, float(timeout)))
    with _THREAD_LOCK:
        _THREAD = None
    return {"ok": True, "message": "worker 已停止", "running": False}


def is_running() -> bool:
    thread = _THREAD
    return bool(thread is not None and thread.is_alive())


def run_forever() -> None:
    """独立进程入口：前台运行，直到收到信号。"""
    import signal

    def _handler(signum, frame):
        _STOP_EVENT.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            continue
    loop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_forever()
