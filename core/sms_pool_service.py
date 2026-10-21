# -*- coding: utf-8 -*-
"""低价号码池业务服务：eligible 账号判定、采购、领取、回收。

边界（SESSION_HANDOFF 第 35.11 节）：
  - 默认读取 SMS_PROVIDER_CHAIN 中已启用的平台，不改动现有 OAuth 取号流程；
    SMS_POOL_PROVIDERS 非空时仅作为号码池专用覆盖。
  - 采购需求由账号库中"未接码的 free 账号"驱动；eligible 为 0 时不采购。
  - 无小时/日预算；累计花费只统计展示。
  - 价格上限是号码池独立 CNY 配置，经实时汇率换算成各平台 USD maxPrice。
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from typing import Any

from core import db
from core import sms_pool_store as store

logger = logging.getLogger(__name__)

# codex_status 中表示"尚未成功接码"的终态集合
_CODEX_PENDING = {"", "not_started", "failed", "stopped", "skipped", "cancelled"}


def _pool_cfg():
    from config import sms_pool as pool_cfg
    return pool_cfg


def pool_providers() -> list[str]:
    """返回号码池平台；未填写覆盖列表时跟随通用接码平台链。"""
    raw = str(getattr(_pool_cfg(), "SMS_POOL_PROVIDERS", "") or "").strip()
    if not raw:
        # The pool should use the platforms selected in the common SMS chain by
        # default.  SMS_POOL_PROVIDERS remains an optional explicit override.
        try:
            from core import sms_provider
            raw = ",".join(sms_provider._provider_chain())
        except Exception as exc:
            logger.warning("[号码池] 读取通用接码平台链失败：%s", exc)
            raw = ""
    if not raw:
        return []
    result: list[str] = []
    for item in raw.split(","):
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value not in result:
            result.append(value)
    return result


def configured_pool_providers() -> list[str]:
    """只返回真正配置了 API key 的号码池平台。"""
    from core import sms_provider
    result = []
    for provider in pool_providers():
        try:
            if sms_provider._configured_provider(provider):
                result.append(provider)
            else:
                logger.info("[号码池] 跳过未配置 API Key 的平台：%s", provider)
        except Exception:
            continue
    return result


def _phone_already_verified(account: dict) -> bool:
    """账号是否已有成功的手机号验证记录。

    数据库把该记录写在 extra_json.codex.phone_activation（见 db._merge_codex_phone_activation），
    而账号列表行只在顶层暴露 extra_json 字符串。只看顶层 phone_activation 会把
    已接码账号误判成"未接码"，进而重复采购号码并重复补跑。
    """
    if not isinstance(account, dict):
        return False
    candidates: list[dict] = []
    try:
        nested = db._phone_activation_from_account_payload(account)
    except Exception:
        nested = {}
    if isinstance(nested, dict) and nested:
        candidates.append(nested)
    top = account.get("phone_activation")
    if isinstance(top, dict) and top:
        candidates.append(top)
    for activation in candidates:
        if str(activation.get("phone_number") or activation.get("phone") or "").strip():
            return True
        if str(activation.get("activation_id") or "").strip():
            return True
    return False


def is_eligible_account(account: dict, busy_ids: set[int] | None = None) -> tuple[bool, str]:
    """判定账号是否可由号码池补跑。返回 (eligible, reason)。"""
    if not isinstance(account, dict):
        return False, "账号不存在"
    acc_id = account.get("id")
    try:
        acc_id_int = int(acc_id)
    except (TypeError, ValueError):
        return False, "账号 ID 非法"

    if bool(account.get("archived")):
        return False, "账号已归档"

    live_status = str(account.get("live_check_status") or "").strip().lower()
    if live_status == "deactivated":
        return False, "账号已废号"

    plan = str(account.get("current_plan_type") or account.get("plan_type") or "").strip().lower()
    if not plan:
        return False, "套餐未知"
    if plan != "free":
        return False, f"套餐不是 free（{plan}）"

    codex_status = str(account.get("codex_status") or "").strip().lower()
    if codex_status == "success":
        return False, "Codex 已成功"
    if codex_status not in _CODEX_PENDING:
        return False, f"Codex 状态不可补跑（{codex_status}）"

    if _phone_already_verified(account):
        return False, "已有手机号验证记录"

    email = str(account.get("email") or "").strip()
    if not email:
        return False, "邮箱为空"

    if busy_ids and acc_id_int in busy_ids:
        return False, "已被号码池占用"

    try:
        from core import codex_retry_service
        if codex_retry_service.is_retrying(email):
            return False, "Codex 补跑正在运行"
    except Exception:
        logger.debug("[号码池] 查询 Codex 补跑状态失败", exc_info=True)

    return True, ""


def _live_check_interval() -> int:
    return max(60, int(getattr(_pool_cfg(), "SMS_POOL_LIVE_CHECK_INTERVAL", 21600) or 21600))


def _live_check_age(account: dict) -> float | None:
    value = str(account.get("live_checked_at") or "").strip()
    if not value:
        return None
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(checked.tzinfo) if checked.tzinfo else datetime.now()
    return max(0.0, (now - checked).total_seconds())


def is_live_check_ready(account: dict) -> bool:
    """只有最近一次明确成功的查活结果才允许接管号码并启动补跑。"""
    if not isinstance(account, dict):
        return False
    status = str(account.get("live_check_status") or "").strip().lower()
    if status != "live" or not bool(account.get("live_check_ok")):
        return False
    age = _live_check_age(account)
    return age is not None and age < _live_check_interval()


def enqueue_live_checks(
    *,
    force: bool = False,
    limit: int = 20,
    trigger: str = "sms_pool_scheduled",
    account_ids: set[int] | None = None,
) -> dict:
    """将候选账号查活任务交给既有后台队列；不在调用线程执行网络检查。"""
    busy_ids = store.active_account_ids()
    try:
        accounts = db.list_accounts(limit=5000)
    except Exception:
        logger.exception("[号码池] 读取查活候选失败")
        return {"queued": 0, "busy": 0, "skipped": 0, "error": "账号库读取失败"}

    max_count = max(1, min(500, int(limit or 20)))
    interval = _live_check_interval()
    retry_cooldown = min(interval, 600)
    # Cloudflare 挑战潮保护：最近 10 分钟内查活大面积落到网络失败时，本轮不再继续
    # 把账号烧成 network_unavailable，等出口冷却后由下一轮接手。
    try:
        recent_cutoff = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        recent_failures = sum(
            1
            for item in accounts
            if str(item.get("live_check_status") or "").strip().lower() == "network_unavailable"
            and str(item.get("live_checked_at") or "") >= recent_cutoff
        )
    except Exception:
        recent_failures = 0
    if not force and recent_failures >= 10:
        logger.warning(
            "[号码池] 最近 10 分钟有 %s 个查活网络失败，本轮暂停查活，等待出口冷却",
            recent_failures,
        )
        return {
            "queued": 0,
            "busy": 0,
            "skipped": 0,
            "deferred": True,
            "recent_network_failures": recent_failures,
            "error": "",
        }
    queued = 0
    busy = 0
    skipped = 0
    try:
        from core import live_check_service
    except Exception:
        logger.exception("[号码池] 查活服务不可用")
        return {"queued": 0, "busy": 0, "skipped": 0, "error": "查活服务不可用"}

    for account in accounts:
        try:
            account_id = int(account.get("id") or 0)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if account_ids is not None and account_id not in account_ids:
            continue
        ok, _reason = is_eligible_account(account, busy_ids)
        if not ok:
            skipped += 1
            continue
        status = str(account.get("live_check_status") or "").strip().lower()
        if status in {"queued", "running", "deactivated"}:
            skipped += 1
            continue
        age = _live_check_age(account)
        if not force:
            if status == "live" and bool(account.get("live_check_ok")) and age is not None and age < interval:
                skipped += 1
                continue
            cooldown = retry_cooldown if status == "failed" else interval
            if age is not None and age < cooldown:
                skipped += 1
                continue
        email = str(account.get("email") or "").strip()
        if not email:
            skipped += 1
            continue
        result = live_check_service.enqueue_account_live_check(
            account_id=account_id,
            email=email,
            trigger=trigger,
            proxy=None,
        )
        if result.get("accepted"):
            queued += 1
        elif result.get("busy"):
            busy += 1
        else:
            skipped += 1
        if queued >= max_count:
            break
    return {"queued": queued, "busy": busy, "skipped": skipped}


def enqueue_scheduled_live_checks() -> dict:
    cfg = _pool_cfg()
    if not bool(getattr(cfg, "SMS_POOL_LIVE_CHECK_ENABLED", False)):
        return {"queued": 0, "busy": 0, "skipped": 0, "disabled": True}
    batch = int(getattr(cfg, "SMS_POOL_LIVE_CHECK_BATCH", 20) or 20)
    return enqueue_live_checks(limit=batch, trigger="sms_pool_scheduled")


def scan_eligible_accounts() -> dict:
    """扫描账号库，返回 eligible / 待核验 两组账号。"""
    busy = store.active_account_ids()
    eligible: list[dict] = []
    pending: list[dict] = []
    deactivated = 0
    live_ready = 0
    needs_live_check = 0
    checking_live = 0
    try:
        accounts = db.list_accounts(limit=5000)
    except Exception:
        logger.exception("[号码池] 读取账号库失败")
        accounts = []

    for account in accounts:
        if str(account.get("live_check_status") or "").strip().lower() == "deactivated":
            possible_candidate = dict(account)
            possible_candidate["live_check_status"] = ""
            if is_eligible_account(possible_candidate, busy)[0]:
                deactivated += 1
        ok, reason = is_eligible_account(account, busy)
        if ok:
            status = str(account.get("live_check_status") or "").strip().lower()
            ready = is_live_check_ready(account)
            live_ready += int(ready)
            needs_live_check += int(not ready and status not in {"queued", "running"})
            checking_live += int(status in {"queued", "running"})
            eligible.append(
                {
                    "id": int(account.get("id")),
                    "email": str(account.get("email") or ""),
                    "plan_type": str(account.get("current_plan_type") or account.get("plan_type") or ""),
                    "codex_status": str(account.get("codex_status") or ""),
                    "live_check_status": status,
                    "live_check_ok": bool(account.get("live_check_ok")),
                    "live_checked_at": str(account.get("live_checked_at") or ""),
                }
            )
        elif reason in {"套餐未知", "已有手机号验证记录"}:
            pending.append(
                {
                    "id": account.get("id"),
                    "email": str(account.get("email") or ""),
                    "reason": reason,
                }
            )
    return {
        "eligible": eligible,
        "pending_review": pending,
        "busy": len(busy),
        "live_summary": {
            "ready": live_ready,
            "waiting": needs_live_check,
            "checking": checking_live,
            "deactivated": deactivated,
        },
    }


def _poll_state_with_staleness() -> dict:
    """给 UI 的轮询快照补上"是否过期"。

    worker 进程停掉后，SQLite 里最后一条心跳会一直留着；直接把它当当前状态
    展示，就会出现"平台全部已暂停"这种假状态。这里按 2 个轮询周期判定过期，
    过期后把整体状态标成 stale，并把各平台标成 unknown。
    """
    state = dict(store.get_poll_state() or {})
    if not state:
        return {"status": "stale", "stale": True, "age_seconds": None,
                "stale_reason": "worker 尚未运行过", "provider_state": {}}

    try:
        interval = max(30, int(getattr(_pool_cfg(), "SMS_POOL_POLL_INTERVAL", 120) or 120))
    except Exception:
        interval = 120
    raw = str(state.get("heartbeat_at") or state.get("last_poll_at") or "").strip()
    age: float | None = None
    if raw:
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
        except ValueError:
            age = None

    stale = age is None or age > max(120, interval * 2)
    state["age_seconds"] = int(age) if age is not None else None
    state["stale"] = bool(stale)
    if stale:
        # 旧快照不代表当前状态，不能继续显示成"已暂停/轮询中"。
        state["status"] = "stale"
        state["stale_reason"] = (
            f"worker 未运行，状态快照已过期（{int(age)} 秒前）" if age is not None
            else "缺少心跳时间"
        )
        previous = state.get("provider_state") or {}
        state["provider_state"] = {
            name: {
                "status": "unknown",
                "last_result": state["stale_reason"],
            }
            for name in previous
        }
    return state


def purchase_capacity() -> dict:
    """计算本次可采购数量：由「并发上限 − 已在途」驱动，而不是固定抢几个。

    硬约束：
      - 同时在手的号码（未分配 + 运行中）不超过 SMS_POOL_WORKERS + SMS_POOL_PREWARM_SIZE；
      - 只有某个并发真正空闲（active 下降）时，下一轮才会补上对应数量的号码；
      - 单轮仍受 SMS_POOL_MAX_ACQUIRE_PER_TICK 限制，避免一次打爆供应商。
    """
    cfg = _pool_cfg()
    scan = scan_eligible_accounts()
    eligible_count = len(scan["eligible"])
    unclaimed = store.count_reservations([store.STATUS_RESERVED])
    active = store.count_reservations(list(store.ACTIVE_STATUSES))

    workers = max(1, int(getattr(cfg, "SMS_POOL_WORKERS", 2) or 2))
    prewarm = max(0, int(getattr(cfg, "SMS_POOL_PREWARM_SIZE", 2) or 0))
    per_tick = max(0, int(getattr(cfg, "SMS_POOL_MAX_ACQUIRE_PER_TICK", 2) or 0))

    # eligible 已排除正在占用号码的账号，因此只需扣除未分配号码，不能再次扣 active。
    gap = max(0, eligible_count - unclaimed)
    # 在途 = 已买到但还没结束的号码。并发是硬上限：在途永远不超过 workers，
    # 只有某个并发真正空闲（任务结束）才会补号；预热也只能占用并发额度，
    # 不允许在 workers 之外多囤号码。
    in_flight = unclaimed + active
    free_slots = max(0, workers - active)
    room = max(0, workers - in_flight)
    target = min(gap, room, per_tick)
    return {
        "eligible": eligible_count,
        "unclaimed": unclaimed,
        "active": active,
        "in_flight": in_flight,
        "free_slots": free_slots,
        "room": room,
        "workers": workers,
        "gap": gap,
        "prewarm": prewarm,
        "per_tick": per_tick,
        "capacity": max(0, target),
        "pending_review": len(scan["pending_review"]),
        "live_summary": scan.get("live_summary") or {},
    }


def resolve_price_limit() -> tuple[Decimal | None, str, Decimal | None]:
    """返回号码池 CNY 上限、汇率来源、换算后的 USD 上限。

    rate 语义为 1 CNY = N USD（与 sms_provider._cny_to_usd_rate 一致）。
    """
    cfg = _pool_cfg()
    raw = str(getattr(cfg, "SMS_POOL_MAX_PRICE_CNY", "") or "").strip()
    if not raw:
        return None, "", None
    try:
        max_cny = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        logger.warning("[号码池] SMS_POOL_MAX_PRICE_CNY 不是有效数字：%s", raw[:40])
        return None, "", None
    if max_cny <= 0:
        return None, "", None

    from core import sms_provider
    try:
        rate, source = sms_provider._cny_to_usd_rate()
    except Exception as exc:
        logger.warning("[号码池] 获取汇率失败，禁止采购：%s", exc)
        return max_cny, "", None
    if rate is None or rate <= 0:
        return max_cny, "", None
    return max_cny, str(source or ""), max_cny * rate


def acquire_one(provider: str) -> dict | None:
    """通过底层 provider 采购一个号码并登记为 reserved。

    使用号码池独立价格上限，不读取 SMS_MAX_PRICE。
    """
    from core import sms_provider

    max_cny, fx_source, max_usd = resolve_price_limit()
    if max_cny is None:
        logger.warning("[号码池] 未配置 SMS_POOL_MAX_PRICE_CNY，拒绝采购")
        return None
    if max_usd is None:
        logger.warning("[号码池] 无有效汇率，拒绝采购")
        return None
    # 1 CNY = N USD；用于把供应商 USD 实价换算回人民币。
    cny_to_usd = max_usd / max_cny if max_cny else None

    service = str(getattr(_pool_cfg(), "SMS_POOL_SERVICE", "") or "").strip() or None
    country = str(getattr(_pool_cfg(), "SMS_POOL_COUNTRY", "") or "").strip() or None

    # 用号码池独立上限覆盖通用 SMS_MAX_PRICE；threading.local 只影响本线程，
    # 采购结束后自动恢复，普通注册链的全局配置不受影响。
    http = sms_provider._http()
    try:
        with sms_provider.price_limit_override(max_cny), sms_provider.voip_rejection_override(
            bool(getattr(_pool_cfg(), "SMS_POOL_REJECT_VOIP_NUMBERS", False))
        ):
            activation_id, phone = sms_provider._acquire_number_for_provider(
                provider, http, service=service, country=country
            )
    except Exception as exc:
        logger.warning(
            "[号码池] 采购失败：provider=%s error=%s: %s",
            provider, type(exc).__name__, str(exc)[:200],
        )
        return None
    finally:
        try:
            http.close()
        except Exception:
            pass

    state = {}
    try:
        state = sms_provider.get_activation_info(activation_id) or {}
    except Exception:
        state = {}

    price_amount = str(state.get("price_amount") or "").strip()
    price_currency = str(state.get("price_currency") or "").strip().upper()
    price_cny = ""
    if price_amount:
        try:
            amount = Decimal(price_amount)
            if price_currency == "CNY":
                price_cny = format(amount, "f")
            elif price_currency == "USD" and cny_to_usd and cny_to_usd > 0:
                price_cny = format(amount / cny_to_usd, "f")
        except (InvalidOperation, TypeError, ValueError):
            price_cny = ""

    reservation = store.register_reservation(
        provider=provider,
        activation_id=activation_id,
        phone=phone,
        service=str(state.get("service") or service or ""),
        country=str(state.get("country") or country or ""),
        price_amount=price_amount,
        price_currency=price_currency,
        price_cny=price_cny,
        fx_rate=format(cny_to_usd, "f") if cny_to_usd else "",
        fx_source=fx_source,
        max_price_cny=format(max_cny, "f"),
        expires_in_seconds=int(
            getattr(_pool_cfg(), "SMS_POOL_ACTIVATION_TTL_SECONDS", 1200) or 0
        ),
        metadata=state,
    )
    if reservation:
        logger.info(
            "[号码池] 已锁定号码：reservation=%s provider=%s region=%s price_cny=%s",
            reservation.get("id"), provider,
            state.get("phone_country_name") or state.get("phone_region") or "unknown",
            price_cny or "unknown",
        )
    return reservation


def release_reservation(reservation_id: int, reason: str, *, code: str = "released") -> dict:
    """按平台协议释放号码并更新状态。"""
    from core import sms_provider

    reservation = store.get_reservation(int(reservation_id))
    if reservation is None:
        return {"ok": False, "error": "号码记录不存在"}
    status = str(reservation.get("status") or "")
    if status in {store.STATUS_COMPLETED, store.STATUS_RELEASED, store.STATUS_EXPIRED}:
        return {"ok": True, "message": f"号码已处于终态：{status}", "status": status}

    activation_id = str(reservation.get("activation_id") or "")
    release_result = ""
    try:
        if code == "completed":
            sms_provider.complete(activation_id)
            release_result = "completed"
        else:
            result = sms_provider.cancel(activation_id)
            release_result = str((result or {}).get("release") or "cancelled")
    except Exception as exc:
        release_result = f"failed: {type(exc).__name__}"
        logger.warning(
            "[号码池] 释放号码失败：reservation=%s activation=%s error=%s",
            reservation_id, activation_id, exc,
        )

    final_status = {
        "completed": store.STATUS_COMPLETED,
        "expired": store.STATUS_EXPIRED,
    }.get(code, store.STATUS_RELEASED)

    updated = store.update_status(
        int(reservation_id),
        final_status,
        failure_code="" if code == "completed" else code,
        failure_reason=str(reason or "")[:400],
        release_result=release_result,
        terminal=True,
    )
    return {
        "ok": True,
        "reservation": updated,
        "release_result": release_result,
        "status": final_status,
    }


def reconcile() -> dict:
    """回收过期未分配号码，并把无法确认状态的运行中号码标记为 orphaned。"""
    expired = store.find_expired_unclaimed()
    expired_count = 0
    for item in expired:
        result = release_reservation(int(item["id"]), "本地有效期到期，自动释放", code="expired")
        if result.get("ok"):
            expired_count += 1

    stale = store.find_stale_active()
    orphaned = 0
    for item in stale:
        # 租约过期说明 worker 已消失；无法确认供应商状态，禁止复用。
        store.update_status(
            int(item["id"]),
            store.STATUS_ORPHANED,
            failure_code="lease_expired",
            failure_reason="worker 租约过期，无法确认供应商状态，已停止复用",
            terminal=True,
        )
        orphaned += 1

    if expired_count or orphaned:
        logger.info("[号码池] 对账完成：过期释放=%s orphaned=%s", expired_count, orphaned)
    return {"expired_released": expired_count, "orphaned": orphaned}


def pool_status() -> dict:
    """号码池运行状态快照，供 UI 展示。"""
    cfg = _pool_cfg()
    capacity = purchase_capacity()
    price_cny, fx_source, max_usd = resolve_price_limit()
    return {
        "enabled": bool(getattr(cfg, "SMS_POOL_ENABLED", False)),
        "paused": bool(getattr(cfg, "SMS_POOL_PAUSED", False)),
        "providers": pool_providers(),
        "configured_providers": configured_pool_providers(),
        "poll_interval": int(getattr(cfg, "SMS_POOL_POLL_INTERVAL", 120) or 120),
        "max_price_cny": str(price_cny) if price_cny is not None else "",
        "max_price_usd": format(max_usd, "f") if max_usd is not None else "",
        "fx_source": fx_source,
        "workers": int(getattr(cfg, "SMS_POOL_WORKERS", 2) or 2),
        "live_check": {
            "enabled": bool(getattr(cfg, "SMS_POOL_LIVE_CHECK_ENABLED", False)),
            "interval": _live_check_interval(),
            "batch": max(1, int(getattr(cfg, "SMS_POOL_LIVE_CHECK_BATCH", 20) or 20)),
        },
        "poll": _poll_state_with_staleness(),
        "capacity": capacity,
        "stats": store.stats(),
    }
