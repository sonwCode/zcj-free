# -*- coding: utf-8 -*-
"""通过 CloakBrowser + Playwright 适配层执行 ChatGPT 注册。"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from config import cloakbrowser as _cfg
from config import email as _email_cfg
from config import twofa as _twofa_cfg
from core.account_export import run_twofa_before_codex, save_account_data, post_register_dwell
from core.otp_utils import mask_otp as _mask_otp
from core.browser_data_saver import BrowserDataSaver
from core.browser_traffic import PlaywrightTrafficTracker
from core.cloakbrowser_driver import build_cloak_driver, close_cloak_driver
from core.email_provider import acquire_email_after_input, wait_for_otp, resolve_email_source
from core.humanize import delay as human_delay

# 复用 Roxy 注册流程里已维护好的页面操作函数。
from core.roxy_registration import (  # noqa: F401
    _maybe_accept, _type_email_address, _submit_email_and_wait_next, _fill_password_page_if_present,
    _clear_otp_inputs, _type_otp, _click_continue, _wait_after_email_otp_submit,
    _click_resend_email_otp, _complete_profile_page, _fetch_chatgpt_session, _check_manual_stop,
    _safe_get,
)

logger = logging.getLogger(__name__)
_CLOAK_CLEANUP_TIMEOUT_SECONDS = 5.0
_ACTIVE_CLOAK_DRIVERS_LOCK = threading.RLock()
_ACTIVE_CLOAK_DRIVERS: dict[int, object] = {}


def _register_active_cloak_driver(driver) -> None:
    with _ACTIVE_CLOAK_DRIVERS_LOCK:
        _ACTIVE_CLOAK_DRIVERS[threading.get_ident()] = driver


def _unregister_active_cloak_driver() -> None:
    with _ACTIVE_CLOAK_DRIVERS_LOCK:
        _ACTIVE_CLOAK_DRIVERS.pop(threading.get_ident(), None)


def _active_cloak_driver(thread_id: int | None):
    if thread_id is None:
        return None
    with _ACTIVE_CLOAK_DRIVERS_LOCK:
        return _ACTIVE_CLOAK_DRIVERS.get(thread_id)


def _current_job_id() -> int | None:
    try:
        from core.registration_service import _THREAD_CTX
    except ImportError:
        return None
    value = getattr(_THREAD_CTX, "job_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bind_job_id(job_id: int | None) -> None:
    if job_id is None:
        return
    try:
        from core.registration_service import _THREAD_CTX
    except ImportError:
        return
    _THREAD_CTX.job_id = int(job_id)


def _clear_job_id() -> None:
    try:
        from core.registration_service import _THREAD_CTX
        delattr(_THREAD_CTX, "job_id")
    except (ImportError, AttributeError):
        pass


def _bounded_cleanup(
    label: str,
    callback,
    on_timeout=None,
    timeout_seconds: float = _CLOAK_CLEANUP_TIMEOUT_SECONDS,
):
    """在 Playwright owner 线程清理，并由 watchdog 解除同步 API 卡死。

    同步 Playwright API 带有 greenlet 线程亲和性；callback 仍在 owner 线程
    执行。watchdog 只负责在超时后终止 driver，让阻塞中的 CDP 调用返回异常，
    避免把隔离线程和外层任务永久锁在 join()。
    """
    if on_timeout is None:
        try:
            return callback()
        except BaseException as exc:
            logger.debug("[Cloak注册] 清理 %s 失败：%s: %s", label, type(exc).__name__, exc)
            return None

    completed = threading.Event()
    timed_out = threading.Event()
    timeout = max(0.5, float(timeout_seconds or _CLOAK_CLEANUP_TIMEOUT_SECONDS))

    def _watchdog() -> None:
        if completed.wait(timeout):
            return
        timed_out.set()
        logger.warning("[Cloak注册] 清理 %s 超时 %.1fs，强制回收浏览器并继续任务收尾", label, timeout)
        try:
            on_timeout()
        except BaseException as fallback_exc:
            logger.debug("[Cloak注册] 清理 %s 兜底失败：%s: %s", label, type(fallback_exc).__name__, fallback_exc)

    watchdog = threading.Thread(target=_watchdog, name=f"cloak-cleanup-watchdog-{label}", daemon=True)
    watchdog.start()
    try:
        return callback()
    except BaseException as exc:
        if not timed_out.is_set():
            logger.debug("[Cloak注册] 清理 %s 失败：%s: %s", label, type(exc).__name__, exc)
        return None
    finally:
        completed.set()
        watchdog.join(0.05)



def _run_cloak_registration_impl(
    email: str | None,
    name: str,
    birthday: str,
    proxy: str = None,
    otp_code: str = None,
    batch_dir: Path | None = None,
    on_email_acquired: Callable[[str], None] | None = None,
    exclude_emails=None,
) -> dict:
    """CloakBrowser 自动化注册入口。"""
    driver = None
    opened = None
    create_acknowledged = False
    openai_password: str | None = None
    traffic_tracker: PlaywrightTrafficTracker | None = None
    data_saver: BrowserDataSaver | None = None
    network_traffic: dict | None = None
    traffic_tracker_stopped = False
    data_saver_stopped = False
    driver_quit = False
    hard_cleanup = False
    try:
        driver, opened = build_cloak_driver(proxy=proxy)
        _register_active_cloak_driver(driver)
        try:
            traffic_tracker = PlaywrightTrafficTracker(driver.context, label="Cloak")
        except Exception as exc:
            # 统计失败不应影响注册主流程。
            logger.warning("[Cloak注册] 初始化浏览器流量统计失败，继续注册：%s: %s", type(exc).__name__, str(exc)[:180])
        data_saver = BrowserDataSaver(label="Cloak")
        if traffic_tracker is not None:
            traffic_tracker.attach_data_saver(data_saver)
        data_saver.install_playwright(driver.context)
        logger.info("[Cloak注册] 开始：%s，profile=%s", email, opened.profile_id)

        otp_after_ts = time.time()
        logger.info("[Cloak注册] 打开登录页：https://chatgpt.com/auth/login")
        _safe_get(driver, "https://chatgpt.com/auth/login", timeout=60, attempts=2, accept_hosts=("chatgpt.com",))
        human_delay("navigate")
        _maybe_accept(driver)
        _check_manual_stop()

        def _email_supplier_after_input() -> str:
            nonlocal email
            _check_manual_stop()
            email = acquire_email_after_input(email, exclude_emails=exclude_emails)
            if on_email_acquired:
                on_email_acquired(email)
            return email

        next_state = _submit_email_and_wait_next(
            driver,
            email,
            attempts=3,
            email_supplier=_email_supplier_after_input,
        )
        _check_manual_stop()

        # 如果邮箱提交后直接进入验证码页，也尝试点击“使用密码继续”进入密码创建页；
        # _fill_password_page_if_present 会在设置成功后返回本次 OpenAI 注册密码。
        openai_password = _fill_password_page_if_present(driver, email, timeout=25)
        _check_manual_stop()
        if openai_password:
            # 密码校验后会重新发送注册 OTP；只接受该阶段之后到达的验证码。
            otp_after_ts = time.time()
            logger.info("[Cloak注册][OTP] 密码阶段完成，重置 OTP 起始时间")

        current_otp = otp_code
        used_otps: set[str] = set()
        try:
            max_otp_attempts = max(3, min(6, int(
                getattr(_email_cfg, "REGISTER_OTP_MAX_ATTEMPTS", 4) or 4
            )))
        except (TypeError, ValueError):
            max_otp_attempts = 4
        otp_submitted_attempts = 0
        otp_resend_count = 0
        otp_final_outcome = "not_submitted"
        for otp_attempt in range(1, max_otp_attempts + 1):
            if current_otp is None:
                logger.info("[Cloak注册][OTP] 等待验证码：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
                try:
                    current_otp = wait_for_otp(
                        email,
                        after_ts=otp_after_ts,
                        exclude_codes=used_otps,
                    )
                except Exception as exc:
                    # 参考 Roxy 流程：after_ts 过滤可能漏掉同一封被重发/时间戳异常的验证码。
                    # 最后一轮也先做一次宽松读取，避免邮件已到达但时间戳偏差导致直接失败。
                    fallback_otp = None
                    try:
                        fallback_otp = wait_for_otp(
                            email,
                            after_ts=0.0,
                            max_wait=15,
                            poll_interval=3,
                            exclude_codes=used_otps,
                        )
                    except Exception:
                        fallback_otp = None
                    if fallback_otp:
                        logger.info(
                            "[Cloak注册][OTP] 取码超时但宽松取到最新验证码，直接重试提交：%s (fallback)",
                            _mask_otp(fallback_otp),
                        )
                        current_otp = fallback_otp
                        continue
                    if otp_attempt >= max_otp_attempts:
                        raise
                    logger.warning(
                        "[Cloak注册][OTP] 一直未收到验证码，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                        otp_attempt + 1,
                        max_otp_attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    otp_after_ts = time.time()
                    otp_resend_count += 1
                    _click_resend_email_otp(driver, timeout=25)
                    human_delay("api")
                    current_otp = None
                    continue
            current_otp = str(current_otp or "").strip()
            if not current_otp:
                raise RuntimeError("邮箱验证码为空")
            used_otps.add(current_otp)
            logger.info("[Cloak注册][OTP] 收到验证码：%s", _mask_otp(current_otp))
            _clear_otp_inputs(driver)
            _type_otp(driver, current_otp)
            _check_manual_stop()
            human_delay("otp_input")
            otp_submitted_attempts += 1
            try:
                _click_continue(driver)
                logger.info("[Cloak注册][OTP] 已提交邮箱验证码，等待资料页或登录态")
            except Exception as exc:
                logger.info("[Cloak注册][OTP] 未找到显式提交按钮，继续等待页面状态：%s", str(exc)[:120])

            outcome = _wait_after_email_otp_submit(driver, timeout=30)
            otp_final_outcome = str(outcome or "unknown")
            logger.info("[Cloak注册][OTP] 提交后状态：%s", outcome)
            if outcome == "accepted":
                logger.info(
                    "[Cloak注册][阶段] 邮箱OTP完成：outcome=accepted submit_attempts=%s resends=%s",
                    otp_submitted_attempts, otp_resend_count,
                )
                break
            if otp_attempt >= max_otp_attempts:
                raise RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")
            otp_after_ts = time.time()
            otp_resend_count += 1
            _click_resend_email_otp(driver, timeout=25)
            human_delay("api")
            current_otp = None

        profile_submitted = _complete_profile_page(driver, name, birthday, timeout=60)
        logger.info("[Cloak注册][阶段] 资料页完成：submitted=%s", bool(profile_submitted))
        if profile_submitted:
            create_acknowledged = True
            human_delay("post_auth")

        session_info = _fetch_chatgpt_session(
            driver,
            timeout=150,
            auto_jump_wait=45,
            email=email,
            password=openai_password,
        )
        access_token = session_info["accessToken"]
        logger.info("[Cloak注册] 已拿到 accessToken：%s", email)
        logger.info("[Cloak注册][阶段] ChatGPT会话完成：access_token=present")

        account_proxy = ((opened.raw or {}).get("proxy_pool_target") if opened else None) or proxy or None
        pre_account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=None,
            email_source=resolve_email_source(email),
            proxy_used=account_proxy,
            batch_dir=batch_dir,
            auto_plan_check=False,
            auto_twofa=False,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "registration_password": openai_password,
                "codex": {"status": "pending", "ok": False, "message": "等待 2FA 完成后执行 Codex"},
            },
        )
        pre_twofa_result = run_twofa_before_codex(
            account_id=pre_account_id,
            email=email,
            access_token=access_token,
            proxy=account_proxy,
        )
        if pre_twofa_result.get("ok"):
            totp_secret = str(pre_twofa_result.get("totp_secret") or "") or None
            logger.info("[Cloak注册][2FA] 注册后 Codex 前设置完成")
        else:
            totp_secret = None
        twofa_blocked = bool(_twofa_cfg.ENABLE_2FA) and not bool(pre_twofa_result.get("ok"))
        codex_result = {
            "status": "skipped",
            "ok": False,
            "message": "ENABLE_CODEX_AUTO=False，跳过 Codex",
        }
        if twofa_blocked:
            codex_result = {
                "status": "failed",
                "ok": False,
                "error_code": str(pre_twofa_result.get("error_code") or "twofa_failed"),
                "message": f"2FA 未完成，已阻止 Codex：{str(pre_twofa_result.get('message') or pre_twofa_result.get('error') or '')[:180]}",
            }
            logger.warning("[Cloak注册][2FA] 2FA 未完成，跳过 Codex：code=%s", codex_result["error_code"])
        else:
            try:
                from config import codex as _codex_cfg
                if bool(getattr(_codex_cfg, "ENABLE_CODEX_AUTO", False)):
                    from core.roxy_codex_oauth import run_roxy_codex_oauth
                    logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=True，2FA 完成后复用当前 CloakBrowser 窗口授权")
                    _check_manual_stop()
                    codex_result = run_roxy_codex_oauth(
                        email,
                        reuse_existing_profile=True,
                        existing_driver=driver,
                        existing_opened=opened,
                        force=True,
                        clear_existing_state=True,
                        registration_password=openai_password,
                    )
                else:
                    logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=False，注册后跳过 Codex OAuth")
            except Exception as exc:
                codex_result = {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

        phone_activation = codex_result.get("phone_activation") if isinstance(codex_result, dict) else None
        phone_status = "-"
        if isinstance(phone_activation, dict):
            phone_status = str(phone_activation.get("status") or ("success" if phone_activation.get("success") else "recorded"))
        logger.info(
            "[Cloak注册][阶段] Codex完成：status=%s ok=%s phone=%s error_code=%s message=%s",
            codex_result.get("status", "-"), bool(codex_result.get("ok")), phone_status,
            codex_result.get("error_code") or "-", str(codex_result.get("message") or "")[:180] or "-",
        )

        # 让 OAuth 收尾请求完成；浏览器关闭有 5 秒 watchdog，超时仍会强制回收。
        post_register_dwell(email, label="Cloak注册")
        if driver and not driver_quit:
            driver_quit = True
            logger.info("[Cloak注册] 成功路径开始有界优雅关闭浏览器")
            try:
                if not close_cloak_driver(driver, timeout_seconds=_CLOAK_CLEANUP_TIMEOUT_SECONDS):
                    logger.warning("[Cloak注册] 优雅关闭未在时限内完成，浏览器已强制回收")
            except Exception as exc:
                logger.warning("[Cloak注册] 有界优雅关闭异常，改为强制回收：%s: %s", type(exc).__name__, str(exc)[:180])
                try:
                    driver.force_kill()
                except Exception as kill_exc:
                    logger.debug("[Cloak注册] 强制回收浏览器失败：%s: %s", type(kill_exc).__name__, str(kill_exc)[:180])
        if traffic_tracker is not None and not traffic_tracker_stopped:
            traffic_tracker_stopped = True
            try:
                network_traffic = traffic_tracker.snapshot_without_browser()
            except Exception as exc:
                logger.warning("[Cloak注册] 非阻塞流量快照失败：%s: %s", type(exc).__name__, str(exc)[:180])
                network_traffic = None
        if data_saver is not None and not data_saver_stopped:
            data_saver_stopped = True
        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=totp_secret,
            email_source=resolve_email_source(email),
            proxy_used=account_proxy,
            batch_dir=batch_dir,
            auto_twofa=False,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "cloakbrowser": {"profile_id": opened.profile_id, "open_result": opened.raw},
                "registration_password": openai_password,
                "codex": codex_result,
                "network_traffic": network_traffic,
            },
        )
        traffic_summary = network_traffic or {}
        codex_status = str(codex_result.get("status") or ("success" if bool(codex_result.get("ok")) else "failed"))
        codex_ok = bool(codex_result.get("ok")) or codex_status == "skipped"
        logger.info(
            "[Cloak注册][摘要] email=%s account_id=%s account=success task=%s codex=%s phone=%s "
            "otp_outcome=%s otp_submit_attempts=%s otp_resends=%s requests=%s failed=%s unfinished=%s",
            email, account_id, "success" if codex_ok else "partial_success", codex_status, phone_status, otp_final_outcome,
            otp_submitted_attempts, otp_resend_count,
            traffic_summary.get("request_count", "-"),
            traffic_summary.get("failed_request_count", "-"),
            traffic_summary.get("unfinished_request_count", "-"),
        )
        return {
            "success": True,
            "task_status": "success" if codex_ok else "partial_success",
            "account_status": "success",
            "codex_status": codex_status,
            "phase": "completed" if codex_ok else "codex",
            "error_code": None if codex_ok else (str(codex_result.get("error_code") or "") or f"codex_{codex_status}"),
            "retryable": (
                not codex_ok
                and codex_status != "deactivated"
                and str(codex_result.get("error_code") or "") not in {"account_deactivated", "account_deleted", "account_banned"}
            ),
            "email": email,
            "account_id": account_id,
            "access_token": access_token,
            "totp_secret": totp_secret,
            "codex": codex_result,
            "network_traffic": network_traffic,
            "error": None if codex_ok else f"Codex 未完成: {codex_result.get('message')}",
        }
    except Exception as exc:
        hard_cleanup = True
        logger.error("[Cloak注册] 失败：%s: %s", type(exc).__name__, exc, exc_info=True)
        logger.debug("[Cloak注册] 失败详情", exc_info=True)
        try:
            if email:
                from core.email_provider import release_email
                from core.roxy_registration import EmailAlreadyRegistered
                if isinstance(exc, EmailAlreadyRegistered):
                    # 邮箱已在 OpenAI 侧存在账号：停用它，避免反复复用产出无密码账号。
                    release_email(email, status="failed", note=f"邮箱已注册: {str(exc)[:150]}")
                else:
                    release_email(email, status="failed" if create_acknowledged else "available", note=f"Cloak注册失败: {str(exc)[:180]}")
        except Exception:
            pass
        return {
            "success": False,
            "task_status": "failed",
            "account_status": "failed",
            "codex_status": "not_started",
            "phase": "registration",
            "error_code": getattr(exc, "error_code", None) or type(exc).__name__.lower(),
            "retryable": bool(getattr(exc, "retryable", not create_acknowledged)) and not create_acknowledged,
            "email": email,
            "network_traffic": network_traffic,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    finally:
        _unregister_active_cloak_driver()
        if hard_cleanup:
            # 失败时 Playwright pipe 可能已经断开；跳过 tracker/data-saver/quit，
            # 直接杀掉 driver 进程树，避免异常任务在收尾阶段再次阻塞。
            if driver and not driver_quit:
                driver_quit = True
                try:
                    logger.info("[Cloak注册] 失败路径硬回收浏览器，跳过断开 pipe 的优雅清理")
                    driver.force_kill()
                except BaseException as exc:
                    logger.debug("[Cloak注册] 失败路径硬回收浏览器失败：%s: %s", type(exc).__name__, exc)
        else:
            # 成功路径统计已完成，直接硬回收浏览器，避免再次触碰可能阻塞的 Playwright API。
            if driver and not driver_quit:
                driver_quit = True
                try:
                    logger.info("[Cloak注册] 成功路径硬回收浏览器，跳过可能阻塞的 tracker/context stop")
                    driver.force_kill()
                except BaseException as exc:
                    logger.debug("[Cloak注册] 成功路径硬回收浏览器失败：%s: %s", type(exc).__name__, exc)


def _run_in_isolated_thread(fn: Callable, *args, **kwargs):
    """在独立线程运行同步 Playwright，并限制线程收尾等待时间。"""
    result_box: dict[str, object] = {}
    error_box: dict[str, BaseException] = {}
    parent_thread_name = threading.current_thread().name
    job_id = _current_job_id()
    inherited_context = contextvars.copy_context()

    def _target() -> None:
        if job_id is not None:
            _bind_job_id(job_id)
        try:
            result_box["value"] = inherited_context.run(fn, *args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - 需要跨线程回传停止异常
            error_box["error"] = exc
        finally:
            if job_id is not None:
                _clear_job_id()

    thread = threading.Thread(target=_target, name=parent_thread_name, daemon=True)
    thread.start()
    # 无限等待任务自然完成（成功或异常），避免硬超时打断 Codex 手机验证等长流程。
    # 参考项目也是直接同步执行，无超时限制。
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def run_cloak_registration(
    email: str | None,
    name: str,
    birthday: str,
    proxy: str = None,
    otp_code: str = None,
    batch_dir: Path | None = None,
    on_email_acquired: Callable[[str], None] | None = None,
    exclude_emails=None,
) -> dict:
    """CloakBrowser 注册入口；同步 Playwright 始终运行在隔离线程。"""
    logger.info("[Cloak注册] 使用隔离线程启动同步 Playwright")
    return _run_in_isolated_thread(
        _run_cloak_registration_impl,
        email=email,
        name=name,
        birthday=birthday,
        proxy=proxy,
        otp_code=otp_code,
        batch_dir=batch_dir,
        on_email_acquired=on_email_acquired,
        exclude_emails=exclude_emails,
    )
