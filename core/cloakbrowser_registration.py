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
from config import twofa as _twofa_cfg
from core.account_export import save_account_data, post_register_dwell
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


def _bounded_cleanup(label: str, callback, on_timeout=None):
    """在 Playwright owner 线程直接完成清理。

    同步 Playwright API 带有 greenlet 线程亲和性；把 callback 放入辅助线程
    会在线程退出后触发 "cannot switch to a different thread"。
    """
    try:
        return callback()
    except BaseException as exc:
        logger.debug("[Cloak注册] 清理 %s 失败：%s: %s", label, type(exc).__name__, exc)
        if on_timeout is not None:
            try:
                on_timeout()
            except BaseException as fallback_exc:
                logger.debug("[Cloak注册] 清理 %s 兜底失败：%s: %s", label, type(fallback_exc).__name__, fallback_exc)
        return None


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
    try:
        driver, opened = build_cloak_driver(proxy=proxy)
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

        current_otp = otp_code
        used_otps: set[str] = set()
        max_otp_attempts = 3
        for otp_attempt in range(1, max_otp_attempts + 1):
            if current_otp is None:
                logger.info("[Cloak注册][OTP] 等待验证码：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
                try:
                    candidate_otp = wait_for_otp(
                        email, after_ts=otp_after_ts, exclude_codes=used_otps
                    )
                    if candidate_otp and str(candidate_otp) in used_otps:
                        raise RuntimeError("重复验证码，等待重新发送")
                    current_otp = candidate_otp
                except Exception as exc:
                    if otp_attempt >= max_otp_attempts:
                        raise
                    logger.warning(
                        "[Cloak注册][OTP] 未收到新验证码，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                        otp_attempt + 1,
                        max_otp_attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    otp_after_ts = time.time()
                    _click_resend_email_otp(driver, timeout=25)
                    human_delay("api")
                    current_otp = None
                    continue
            used_otps.add(str(current_otp))
            logger.info("[Cloak注册][OTP] 收到验证码：%s", current_otp)
            _clear_otp_inputs(driver)
            _type_otp(driver, current_otp)
            human_delay("otp_input")
            try:
                _click_continue(driver)
            except Exception as exc:
                logger.info("[Cloak注册][OTP] 未找到显式提交按钮，继续等待页面状态：%s", str(exc)[:120])

            outcome = _wait_after_email_otp_submit(driver, timeout=10)
            if outcome == "accepted":
                break
            if otp_attempt >= max_otp_attempts:
                raise RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")
            otp_after_ts = time.time()
            _click_resend_email_otp(driver, timeout=25)
            human_delay("api")
            current_otp = None

        profile_submitted = _complete_profile_page(driver, name, birthday, timeout=60)
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

        if _twofa_cfg.ENABLE_2FA:
            logger.warning("[Cloak注册] 当前 CloakBrowser 自动化路径暂不执行 2FA 设置，已跳过")
        totp_secret = None

        codex_result = {
            "status": "skipped",
            "ok": False,
            "message": "ENABLE_CODEX_AUTO=False，跳过 Codex",
        }
        try:
            from config import codex as _codex_cfg
            if bool(getattr(_codex_cfg, "ENABLE_CODEX_AUTO", False)):
                from core.roxy_codex_oauth import run_roxy_codex_oauth
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=True，复用当前 CloakBrowser 窗口执行 Codex 授权")
                _check_manual_stop()
                codex_result = run_roxy_codex_oauth(
                    email,
                    reuse_existing_profile=True,
                    existing_driver=driver,
                    existing_opened=opened,
                    force=True,
                    clear_existing_state=True,
                    # 账号要到本轮注册收尾才落库，Codex 授权必须用刚设好的密码，
                    # 否则密码登录会被静默跳过。
                    registration_password=openai_password,
                )
            else:
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=False，注册后跳过 Codex OAuth")
        except Exception as exc:
            codex_result = {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

        # 统计注册浏览器关闭前的完整会话；注册后停留期间的网络请求也计入。
        post_register_dwell(email, label="Cloak注册")
        if traffic_tracker is not None and not traffic_tracker_stopped:
            traffic_tracker_stopped = True
            network_traffic = _bounded_cleanup("traffic_tracker.stop", traffic_tracker.stop)
        if data_saver is not None and not data_saver_stopped:
            data_saver_stopped = True
            _bounded_cleanup("data_saver.stop", data_saver.stop)
        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=totp_secret,
            email_source=resolve_email_source(email),
            proxy_used=((opened.raw or {}).get("proxy_pool_target") if opened else None) or proxy or None,
            batch_dir=batch_dir,
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
        codex_status = str(codex_result.get("status") or ("success" if bool(codex_result.get("ok")) else "failed"))
        codex_ok = bool(codex_result.get("ok")) or codex_status == "skipped"
        return {
            "success": True,
            "task_status": "success" if codex_ok else "partial_success",
            "account_status": "success",
            "codex_status": codex_status,
            "phase": "completed" if codex_ok else "codex",
            "error_code": None if codex_ok else f"codex_{codex_status}",
            "retryable": not codex_ok and codex_status != "deactivated",
            "email": email,
            "account_id": account_id,
            "access_token": access_token,
            "totp_secret": totp_secret,
            "codex": codex_result,
            "network_traffic": network_traffic,
            "error": None if codex_ok else f"Codex 未完成: {codex_result.get('message')}",
        }
    except Exception as exc:
        logger.error("[Cloak注册] 失败：%s: %s", type(exc).__name__, exc)
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
        if traffic_tracker is not None and not traffic_tracker_stopped:
            traffic_tracker_stopped = True
            _bounded_cleanup("traffic_tracker.stop", traffic_tracker.stop)
        if data_saver is not None and not data_saver_stopped:
            data_saver_stopped = True
            _bounded_cleanup("data_saver.stop", data_saver.stop)
        if driver and not driver_quit and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
            driver_quit = True
            close_cloak_driver(driver)


def _run_in_isolated_thread(fn: Callable, *args, **kwargs):
    """在没有外部 asyncio loop 的独立线程运行同步 Playwright 流程。"""
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

    thread = threading.Thread(target=_target, name=parent_thread_name, daemon=False)
    thread.start()
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
