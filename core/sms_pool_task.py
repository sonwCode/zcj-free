# -*- coding: utf-8 -*-
"""低价号码池：单个"已锁定号码 + free 账号"的独立手机验证任务。

边界（SESSION_HANDOFF 第 35.11 节）：
  - 号码由号码池提前采购并锁定，任务本身不再向供应商取号。
  - 复用现有 Codex 补跑链路（codex_retry_service.run_worker），不新建取号流程。
  - 通过 sms_provider.activation_override() 让 OAuth 流程复用已锁定号码；
    该覆盖是线程级局部状态，普通注册链完全不受影响。
  - 表内只保存脱敏号码与号码哈希；完整号码只存在于进程内激活状态。
    服务重启后内存状态丢失时按 orphaned 处理，不做猜测也不重试。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from core import sms_pool_store as store
from core import sms_pool_log as pool_log
from core import sms_provider

logger = logging.getLogger(__name__)

# 网络/代理故障只说明当前执行链不可用，不能证明已锁定号码有问题。
# 熔断状态只驻留在 worker 进程内，服务重启后从关闭状态重新探测。
_NETWORK_CIRCUIT_LOCK = threading.RLock()
_NETWORK_FAILURE_STREAK = 0
_NETWORK_CIRCUIT_UNTIL = 0.0
_NETWORK_LAST_ERROR = ""

# 上游拒码 -> 号码池失败分类。顺序即优先级。
_FAILURE_CODES: tuple[tuple[str, str], ...] = (
    ("phone_fraud_guard", "phone_fraud_guard"),
    ("fraud_guard", "fraud_guard"),
    ("voip_phone_disallowed", "voip_phone_disallowed"),
    ("phone_number_in_use", "phone_number_in_use"),
    ("session_circuit_open", "session_circuit_open"),
    ("cloudflare_challenge", "cloudflare_challenge"),
    ("no_balance", "no_balance"),
    ("余额不足", "no_balance"),
    ("no_numbers", "no_numbers"),
    ("暂无可用号码", "no_numbers"),
    ("没有可用号码", "no_numbers"),
    ("code_timeout", "code_timeout"),
    ("codex_password_incorrect", "codex_password_incorrect"),
    ("incorrect email address or password", "codex_password_incorrect"),
    ("等待", "code_timeout"),
)

_LEASE_HEARTBEAT_SECONDS = 60

_RELEASE_FAILURE_CODES = frozenset({
    "phone_fraud_guard", "fraud_guard", "voip_phone_disallowed",
    "phone_number_in_use", "number_rejected", "sms_number_rejected",
    "code_rejected", "sms_code_rejected", "code_timeout", "sms_code_timeout",
    # 账号密码被服务端拒绝：号码根本没被用于手机验证，必须立即释放，
    # 不能因为“未确认失败”而重试两次、把已付费号码拖成 orphaned。
    "codex_password_incorrect",
})
_EXPLICIT_PHONE_ERROR_MARKERS = (
    "voip_phone_disallowed", "phone_fraud_guard", "fraud_guard",
    "suspicious behavior from phone", "phone_in_use", "phone number already in use",
    "invalid_phone", "invalid phone", "not a valid phone", "phone number is not valid",
    "already used", "手机号无效", "手机号已被使用",
)
_TRANSIENT_ERROR_MARKERS = (
    "connectionerror", "proxyerror", "proxy connect", "connect tunnel failed",
    "connection reset", "connection refused", "curl:", "http 502", "http 503",
    "http 504", "page.goto", "net::err_", "timed out", "timeout waiting",
    "service unavailable", "browser_session_circuit_open", "circuit open",
    "cloudflare_challenge", "codex_mfa_step_stalled", "email_submit_stalled",
    "http error 403", "http 403", "status=403", "access denied",
)


def _network_circuit_config() -> tuple[int, int]:
    try:
        from config import sms_pool as cfg

        threshold = max(
            1, int(getattr(cfg, "SMS_POOL_NETWORK_FAILURE_THRESHOLD", 2) or 2)
        )
        cooldown = max(
            60, int(getattr(cfg, "SMS_POOL_NETWORK_COOLDOWN_SECONDS", 300) or 300)
        )
        return threshold, cooldown
    except Exception:
        return 2, 300


def network_circuit_state() -> dict[str, Any]:
    """返回号码池网络熔断状态，供采购/分配 worker 使用。"""
    global _NETWORK_FAILURE_STREAK, _NETWORK_CIRCUIT_UNTIL
    now = time.monotonic()
    with _NETWORK_CIRCUIT_LOCK:
        # 冷却结束后允许一次新的探测，不把旧故障计数带入下一窗口。
        if _NETWORK_CIRCUIT_UNTIL and now >= _NETWORK_CIRCUIT_UNTIL:
            _NETWORK_CIRCUIT_UNTIL = 0.0
            _NETWORK_FAILURE_STREAK = 0
        remaining = max(0, int(_NETWORK_CIRCUIT_UNTIL - now))
        return {
            "open": remaining > 0,
            "remaining": remaining,
            "failure_streak": int(_NETWORK_FAILURE_STREAK),
            "last_error": str(_NETWORK_LAST_ERROR or "")[:240],
        }


def note_transient_network_failure(message: object) -> dict[str, Any]:
    """累计一次基础设施故障，达到阈值后暂停新采购和新分配。"""
    global _NETWORK_FAILURE_STREAK, _NETWORK_CIRCUIT_UNTIL, _NETWORK_LAST_ERROR
    threshold, cooldown = _network_circuit_config()
    text = str(message or "未提供错误信息").replace("\n", " ").strip()[:240]
    with _NETWORK_CIRCUIT_LOCK:
        _NETWORK_FAILURE_STREAK += 1
        _NETWORK_LAST_ERROR = text
        opened = False
        if _NETWORK_FAILURE_STREAK >= threshold:
            was_open = _NETWORK_CIRCUIT_UNTIL > time.monotonic()
            _NETWORK_CIRCUIT_UNTIL = time.monotonic() + cooldown
            opened = not was_open
        state = network_circuit_state()
    if opened:
        logger.warning(
            "[号码池] 网络故障熔断：连续失败=%s，暂停采购和分配 %ss；错误=%s",
            state["failure_streak"], cooldown, text,
        )
    return state


def note_successful_network_result() -> None:
    """一次完整成功的补跑证明链路恢复，清除熔断计数。"""
    global _NETWORK_FAILURE_STREAK, _NETWORK_CIRCUIT_UNTIL, _NETWORK_LAST_ERROR
    with _NETWORK_CIRCUIT_LOCK:
        _NETWORK_FAILURE_STREAK = 0
        _NETWORK_CIRCUIT_UNTIL = 0.0
        _NETWORK_LAST_ERROR = ""


def _classify_failure(message: object) -> str:
    """把上游错误文本映射成稳定的失败分类，便于筛选与统计。"""
    text = str(message or "").lower()
    for needle, code in _FAILURE_CODES:
        if needle in text:
            return code
    if "timeout" in text or "超时" in text:
        return "code_timeout"
    return "failed"


def _should_release_number(result: dict, failure_code: str, message: str) -> bool:
    """只有明确手机号/验证码终态错误才允许取消已锁定号码。"""
    error_code = str(result.get("error_code") or "").strip().lower()
    if error_code in _RELEASE_FAILURE_CODES:
        return True
    if str(failure_code or "") in _RELEASE_FAILURE_CODES:
        return True
    text = str(message or "").lower()
    return any(marker in text for marker in _EXPLICIT_PHONE_ERROR_MARKERS)


def _is_transient_failure(result: dict, message: str) -> bool:
    """识别代理、网络、浏览器基础设施故障；这些故障不能证明号码失败。"""
    error_code = str(result.get("error_code") or "").strip().lower()
    if error_code in _RELEASE_FAILURE_CODES:
        return False
    if result.get("retryable") is True:
        return True
    text = str(message or "").lower()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def _hold_without_release(reservation_id: int, reason: str, code: str) -> str:
    """非成功收尾一律释放号码。

    之前为了"不误伤可能仍可用的号码"只标 orphaned、不取消，结果已付费号码长期
    挂在库里看不出结论。现在统一处理：先向供应商取消，成功记 released；
    取消失败（或进程内已无激活）也标 released 并写明 release_result，
    保证"没成功的一个都不留着"。
    """
    row = store.get_reservation(int(reservation_id)) or {}
    activation_id = str(row.get("activation_id") or "").strip()
    released = False
    release_note = "no_activation"
    if activation_id:
        try:
            if _online(activation_id):
                from core import sms_provider as _sp

                _sp.cancel(activation_id, background=False)
                released = True
                release_note = "cancelled"
            else:
                released = True
                release_note = "activation_not_in_memory"
        except Exception as exc:
            release_note = f"cancel_failed: {type(exc).__name__}: {str(exc)[:80]}"
    updated = store.update_status(
        int(reservation_id),
        store.STATUS_RELEASED if released else store.STATUS_ORPHANED,
        failure_code=code,
        failure_reason=reason[:800],
        release_result=release_note,
        terminal=True,
    )
    return str(
        (updated or {}).get("status")
        or (store.STATUS_RELEASED if released else store.STATUS_ORPHANED)
    )


def _recover_phone(activation_id: str) -> str:
    """从进程内激活状态恢复完整号码。

    号码池表只保存脱敏号码，完整号码不落库；服务重启后这里返回空字符串，
    调用方必须按 orphaned 收口，不允许回退成"重新取号"。
    """
    try:
        state = sms_provider.get_activation_info(activation_id) or {}
    except Exception:
        return ""
    return str(state.get("phone_number") or "").strip()


def _online(activation_id: str) -> bool:
    """号码是否仍在进程内激活状态中（用于判断是否已被上游流程释放）。"""
    try:
        return bool(sms_provider.get_activation_info(activation_id))
    except Exception:
        return False


def _finish_request(account_id: int, ok: bool) -> None:
    """把该账号的手动补跑请求收口，避免队列里留下永不完成的记录。"""
    try:
        store.finish_request_for_account(
            int(account_id), store.REQUEST_DONE if ok else store.REQUEST_FAILED
        )
    except Exception:
        logger.debug("号码池请求状态收口失败", exc_info=True)


class _LeaseHeartbeat:
    """任务运行期间刷新本地租约，避免长流程被对账误判为 orphaned。"""

    def __init__(self, reservation_id: int) -> None:
        self._reservation_id = int(reservation_id)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def _beat() -> None:
            while not self._stop.wait(_LEASE_HEARTBEAT_SECONDS):
                try:
                    store.touch_lease(self._reservation_id)
                except Exception:
                    logger.debug("号码池租约刷新失败", exc_info=True)

        self._thread = threading.Thread(
            target=_beat, name=f"sms-pool-lease-{self._reservation_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def run_pool_task(reservation_id: int, account: dict) -> dict:
    """为一个已锁定号码和一个 eligible 账号执行一次手机验证。

    返回值始终是结构化 dict，不向调用线程抛异常。
    """
    reservation = store.get_reservation(int(reservation_id))
    if reservation is None:
        return {"ok": False, "error": "号码记录不存在", "reservation_id": reservation_id}

    account_id = account.get("id")
    email = str(account.get("email") or "").strip()
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "账号 ID 非法", "reservation_id": reservation_id}

    status = str(reservation.get("status") or "")
    if status == store.STATUS_RESERVED:
        claimed = store.claim_for_account(int(reservation_id), account_id, email)
        if claimed is None:
            return {
                "ok": False,
                "skipped": "claim_conflict",
                "account_id": account_id,
                "reservation_id": reservation_id,
            }
    elif status == store.STATUS_CLAIMED and int(reservation.get("account_id") or 0) == account_id:
        # worker 会在分配前先做一次原子领取；这里直接接管该次任务，
        # 不再重复领取，避免把已经归属本账号的号码判成竞争失败。
        claimed = reservation
    else:
        return {
            "ok": False,
            "skipped": "not_reserved",
            "status": status,
            "reservation_id": reservation_id,
        }

    from core import codex_retry_service

    # 同一邮箱已有补跑在跑时不再分配，避免两个流程同时操作同一账号。
    try:
        if codex_retry_service.is_retrying(email):
            return {
                "ok": False,
                "skipped": "already_retrying",
                "email": email,
                "reservation_id": reservation_id,
            }
    except Exception:
        logger.debug("号码池检查补跑状态失败", exc_info=True)

    activation_id = str(claimed.get("activation_id") or "")
    masked = str(claimed.get("phone_mask") or "")
    phone = _recover_phone(activation_id)
    if not phone:
        # 进程内状态已丢失（例如服务重启）：无法安全复用，禁止二次分配。
        store.update_status(
            int(reservation_id),
            store.STATUS_ORPHANED,
            failure_code="activation_state_missing",
            failure_reason="进程内激活状态缺失，无法恢复号码，已停止复用",
            terminal=True,
        )
        logger.warning(
            "[号码池] 激活状态缺失，标记 orphaned：reservation=%s activation=%s 号码=%s",
            reservation_id, activation_id, masked,
        )
        return {"ok": False, "error": "activation_state_missing", "reservation_id": reservation_id}

    # 先保存带完整号码的进程内快照；号码池表只保存脱敏号码，
    # 上游失败后激活可能已经被清理，仍需据此写入号码级质量账本。
    activation_snapshot = sms_provider.get_activation_info(activation_id) or {
        "activation_id": activation_id,
        "phone_number": phone,
        "provider": claimed.get("provider") or "",
        "service": claimed.get("service") or "",
        "country": claimed.get("country") or "",
    }

    if not codex_retry_service.reserve(email):
        # 极小概率竞态：正好在检查后被别的流程占用。号码保持 claimed，
        # 由租约与对账收口，不在这里取消，避免浪费一个已付费号码。
        logger.info("[号码池] 补跑被占用，本轮不执行：email=%s reservation=%s", email, reservation_id)
        return {
            "ok": False,
            "skipped": "reserve_failed",
            "email": email,
            "reservation_id": reservation_id,
        }

    store.update_status(int(reservation_id), store.STATUS_RUNNING)
    logger.info(
        "[号码池] 开始补跑：reservation=%s account=%s 号码=%s provider=%s",
        reservation_id, account_id, masked, claimed.get("provider") or "-",
    )
    pool_log.append_task(
        int(reservation_id), email,
        f"[开始] account={account_id} 号码={masked} provider={claimed.get('provider') or '-'} "
        f"activation={activation_id}",
    )

    heartbeat = _LeaseHeartbeat(int(reservation_id))
    heartbeat.start()
    result: dict[str, Any] = {}
    try:
        from core import codex_retry_service as retry_service

        # 线程级接管：OAuth 流程内部的 acquire_number() 会直接复用该号码，
        # 不会再向供应商取号，也不会走 provider 冷却与质量排序。
        with sms_provider.activation_override(activation_id, phone):
            result = retry_service.run_worker(
                email,
                clear_log=False,
                target_log_path=str(pool_log.task_log_path(int(reservation_id), email)),
                pool_activation={
                    "activation_id": activation_id,
                    "phone_number": phone,
                    "reservation_id": int(reservation_id),
                },
            ) or {}
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
        logger.exception("[号码池] 补跑异常：reservation=%s", reservation_id)
    finally:
        heartbeat.stop()

    ok = bool(result.get("ok"))
    message = str(result.get("message") or result.get("error") or "")
    pool_log.append_task(
        int(reservation_id), email,
        ("[结果] 成功" if ok else f"[结果] 失败 {message[:200]}"),
    )
    _finish_request(account_id, ok)

    if ok:
        note_successful_network_result()
        # 上游已 report_success + complete；这里只收口本地状态。
        release = _finalize(
            int(reservation_id), activation_id, "手机验证成功", code="completed", release=False
        )
        logger.info("[号码池] 补跑成功：reservation=%s account=%s 状态=%s", reservation_id, account_id, release)
        return {
            "ok": True,
            "reservation_id": reservation_id,
            "account_id": account_id,
            "email": email,
            "status": store.STATUS_COMPLETED,
        }

    failure_code = _classify_failure(message)
    if failure_code == "voip_phone_disallowed":
        try:
            sms_provider.report_failure(
                activation_id,
                message or failure_code,
                category="number_rejected",
                state=activation_snapshot,
            )
        except Exception:
            logger.debug("[号码池] 写入 VoIP 号码质量记录失败", exc_info=True)
    # OAuth 失败路径已经释放并遗忘该激活时，不再重复调用供应商取消接口。
    already_released = not _online(activation_id)

    # 网络、代理、浏览器和其他无法证明号码失败的错误：保留激活，
    # 回到 reserved 队列等待下一轮重试；达到上限后只标记待核对，仍不取消。
    if not already_released and not _should_release_number(result, failure_code, message):
        transient_failure = _is_transient_failure(result, message)
        if transient_failure:
            note_transient_network_failure(message)
        retry_code = "transient_network" if transient_failure else "unconfirmed_failure"
        retry = store.requeue_for_retry(
            int(reservation_id),
            message or "未确认的补跑失败",
            code=retry_code,
            max_retries=2,
        )
        if retry and retry.get("status") == store.STATUS_RESERVED:
            logger.warning(
                "[号码池] 补跑暂时失败，未释放号码并排队重试：reservation=%s account=%s code=%s retry=%s",
                reservation_id, account_id, retry_code, retry.get("retry_count"),
            )
            return {
                "ok": False,
                "reservation_id": reservation_id,
                "account_id": account_id,
                "email": email,
                "failure_code": retry_code,
                "message": message[:300],
                "status": store.STATUS_RESERVED,
                "retry_pending": True,
            }
        held_status = _hold_without_release(
            int(reservation_id),
            message or "未确认的补跑失败",
            retry_code if not retry else "transient_retry_exhausted",
        )
        logger.warning(
            "[号码池] 补跑失败但无法确认号码状态，未释放：reservation=%s account=%s code=%s status=%s",
            reservation_id, account_id, retry_code, held_status,
        )
        return {
            "ok": False,
            "reservation_id": reservation_id,
            "account_id": account_id,
            "email": email,
            "failure_code": retry_code,
            "message": message[:300],
            "status": held_status,
            "retry_pending": False,
        }

    release = _finalize(
        int(reservation_id),
        activation_id,
        message or "手机验证失败",
        code=failure_code,
        release=not already_released,
    )
    logger.warning(
        "[号码池] 补跑失败：reservation=%s account=%s code=%s release=%s",
        reservation_id, account_id, failure_code, release,
    )
    return {
        "ok": False,
        "reservation_id": reservation_id,
        "account_id": account_id,
        "email": email,
        "failure_code": failure_code,
        "message": message[:300],
        "status": store.STATUS_RELEASED,
    }


def _finalize(
    reservation_id: int, activation_id: str, reason: str, *, code: str, release: bool
) -> str:
    """按结果收口号码状态；返回最终状态字符串。"""
    if code == "completed":
        if not _online(activation_id):
            # 上游流程成功后已经调用过 complete() 并清理了激活状态，
            # 这里只写本地终态，避免对同一激活重复调用供应商接口。
            updated = store.update_status(
                reservation_id,
                store.STATUS_COMPLETED,
                release_result="completed_by_oauth",
                terminal=True,
            )
            return str((updated or {}).get("status") or store.STATUS_COMPLETED)
        from core import sms_pool_service as service
        result = service.release_reservation(reservation_id, reason, code="completed")
        return str(result.get("status") or store.STATUS_COMPLETED)

    if not release:
        # 号码已被上游流程释放，只需写入本地终态与失败分类。
        updated = store.update_status(
            reservation_id,
            store.STATUS_RELEASED,
            failure_code=code,
            failure_reason=reason[:400],
            release_result="released_by_oauth",
            terminal=True,
        )
        return str((updated or {}).get("status") or store.STATUS_RELEASED)

    from core import sms_pool_service as service
    result = service.release_reservation(reservation_id, reason, code=code)
    return str(result.get("status") or store.STATUS_RELEASED)


__all__ = [
    "run_pool_task",
    "network_circuit_state",
    "note_transient_network_failure",
    "note_successful_network_result",
]
