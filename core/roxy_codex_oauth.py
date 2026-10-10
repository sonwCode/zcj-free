# -*- coding: utf-8 -*-
"""通过 RoxyBrowser 指纹浏览器执行 Codex OAuth 授权。"""
from __future__ import annotations

import json
import logging
import random
import time
from contextvars import ContextVar
from urllib.parse import urlparse

import phonenumbers
from phonenumbers import geocoder as phone_geocoder

from config import roxybrowser as _roxy_cfg
from core.email_provider import wait_for_otp
from core.humanize import delay as human_delay
from core.stop_control import check_stop_requested as _check_stop_requested, sleep as _stop_sleep
from core import sms_provider
from core.openai_auth import (
    AccountUnusableError,
    detect_account_unusable_response_body,
    detect_account_unusable_text,
)
from core.roxybrowser_client import RoxyBrowserClient
from core import codex_oauth as _codex_proto
from core.roxy_registration import (
    _build_driver,
    _center_browser_window,
    _click_any,
    _click_continue,
    _find_any,
    _maybe_accept,
    _human_click,
    _human_type_text,
    _type_any,
    _type_email_address,
    _submit_email_step,
    _recover_email_submit_if_stuck,
    _click_email_entry_option,
    _type_otp,
    _clear_otp_inputs,
    _visible,
    _bounded_stop_cleanup,
    _email_otp_page_state,
    _is_email_verification_page,
    _is_login_password_page,
    _click_passwordless_signup_if_present,
)

_base_logger = logging.getLogger(__name__)
_CODEX_BROWSER_KIND: ContextVar[str] = ContextVar("codex_browser_kind", default="Roxy")
_REGISTRATION_PASSWORD_CACHE: dict[str, str] = {}


def remember_registration_password(email: str, password: str) -> None:
    key = str(email or "").strip().lower()
    if key and password:
        _REGISTRATION_PASSWORD_CACHE[key] = str(password)


def _codex_prefix() -> str:
    return f"[Codex][{_CODEX_BROWSER_KIND.get()}]"


def _codex_driver_name() -> str:
    return _CODEX_BROWSER_KIND.get()


def _detect_browser_kind(opened=None) -> str:
    try:
        raw = getattr(opened, "raw", None) or {}
        if isinstance(raw, dict) and str(raw.get("driver") or "").lower().startswith("cloak"):
            return "Cloak"
    except Exception:
        pass
    return "Roxy"


class _CodexLogger:
    """把流程内部统一占位前缀替换成当前真实浏览器类型。"""
    def __init__(self, base):
        self._base = base

    def _msg(self, msg):
        return str(msg).replace("[Codex][Browser]", _codex_prefix())

    def debug(self, msg, *args, **kwargs):
        return self._base.debug(self._msg(msg), *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        return self._base.info(self._msg(msg), *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        return self._base.warning(self._msg(msg), *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        return self._base.error(self._msg(msg), *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        return self._base.exception(self._msg(msg), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._base, name)


logger = _CodexLogger(_base_logger)


def _is_callback_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme in ("http", "https")
            and parsed.hostname in ("localhost", "127.0.0.1")
            and parsed.port == 1455
            and parsed.path == "/auth/callback"
        )
    except Exception:
        return False


def _extract_callback_url_from_performance_log(driver) -> str:
    """从 Chrome performance log 捕获短暂的 localhost callback 请求。"""
    try:
        entries = driver.get_log("performance") or []
    except Exception:
        return ""
    for entry in entries:
        try:
            raw_message = entry.get("message") if isinstance(entry, dict) else entry
            outer = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
            message = outer.get("message") if isinstance(outer, dict) else None
            if not isinstance(message, dict):
                continue
            method = str(message.get("method") or "")
            params = message.get("params") or {}
            if method == "Network.requestWillBeSent":
                candidates = [
                    (params.get("request") or {}).get("url"),
                    (params.get("redirectResponse") or {}).get("url"),
                ]
            elif method == "Network.responseReceived":
                candidates = [(params.get("response") or {}).get("url")]
            else:
                continue
            for candidate in candidates:
                candidate = str(candidate or "")
                if _is_callback_url(candidate):
                    logger.info(
                        "[Codex][Browser] 从 Chrome performance log 捕获 callback URL：%s",
                        candidate[:160],
                    )
                    return candidate
        except Exception:
            continue
    return ""


def _extract_callback_url_from_page(driver) -> str:
    """从当前页面提取 OAuth callback URL。

    浏览器跳转到 http://localhost:1455/auth/callback?... 时，本地没有服务监听会显示
    chrome-error://chromewebdata/。地址栏可能变成 chrome-error，但 Chromium 的
    performance navigation entry 仍保留原始 callback URL，可直接提取后提交 CPA。
    """
    try:
        current = str(driver.current_url or "")
        if _is_callback_url(current):
            return current
    except Exception:
        pass
    try:
        urls = driver.execute_script(r"""
        const out = [];
        const push = v => { if (v && typeof v === 'string') out.push(v); };
        try { push(location.href); } catch (e) {}
        try { push(document.URL); } catch (e) {}
        try { push(document.documentURI); } catch (e) {}
        try { for (const e of performance.getEntriesByType('navigation')) push(e.name); } catch (e) {}
        try { for (const e of performance.getEntries()) push(e.name); } catch (e) {}
        return [...new Set(out)];
        """) or []
        for url in urls:
            if _is_callback_url(str(url)):
                logger.info("[Codex][Browser] 已从浏览器性能记录提取 callback URL：%s", str(url)[:160])
                return str(url)
    except Exception as exc:
        logger.debug("[Codex][Browser] 从页面提取 callback URL 失败：%s", exc)
    callback = _extract_callback_url_from_performance_log(driver)
    if callback:
        return callback
    return ""


def _extract_callback_url_from_any_window(driver) -> str:
    original_handle = None
    try:
        original_handle = driver.current_window_handle
    except Exception:
        pass
    try:
        found = _extract_callback_url_from_page(driver)
        if found:
            return found
        for handle in list(getattr(driver, "window_handles", []) or []):
            if handle == original_handle:
                continue
            try:
                driver.switch_to.window(handle)
                found = _extract_callback_url_from_page(driver)
                if found:
                    return found
            except Exception:
                continue
    except Exception:
        pass
    finally:
        if original_handle is not None:
            try:
                driver.switch_to.window(original_handle)
            except Exception:
                pass
    return ""


def _wait_for_callback(driver, timeout: int | None = None) -> str:
    end = time.time() + (timeout or int(_roxy_cfg.ROXY_CODEX_CALLBACK_TIMEOUT))
    last_url = ""
    while time.time() < end:
        try:
            current = str(driver.current_url or "")
            if current != last_url:
                logger.debug("[Codex][Browser] 当前 URL: %s", current)
                last_url = current
            callback = _extract_callback_url_from_any_window(driver)
            if callback:
                return callback
        except Exception:
            pass
        _stop_sleep(0.5)
    raise RuntimeError(f"等待 Codex callback 超时，最后 URL={last_url}")


def _click_if_present(driver, selectors: list[str], timeout: int = 3) -> bool:
    try:
        _click_any(driver, selectors, timeout=timeout)
        return True
    except Exception:
        return False


def _maybe_click_passwordless_after_email(driver, email: str, timeout: int = 18) -> None:
    """
    Codex OAuth 提交邮箱后也可能跳到 /log-in/password 或 /create-account/password。
    优先点击“使用一次性验证码/one-time code”入口，进入邮箱验证码页。
    """
    end = time.time() + timeout
    last_url = ""
    clicked = False
    while time.time() < end:
        try:
            if _is_email_verification_page(driver):
                if clicked:
                    logger.info("[Codex][Browser] 一次性验证码入口已进入邮箱验证码页")
                return
            url = str(driver.current_url or "")
            if url != last_url:
                logger.info("[Codex][Browser] 提交邮箱后检测密码/OTP 跳转：url=%s", url or "-")
                last_url = url
            lower = url.lower()
            if any(x in lower for x in ("phone", "workspace", "consent", "authorize", "localhost:1455")):
                return
            if "/password" in lower or "auth.openai.com" in lower:
                result = _click_passwordless_signup_if_present(driver)
                if result.get("ok"):
                    clicked = True
                    logger.info("[Codex][Browser] 已点击一次性验证码入口：email=%s detail=%s", email, result)
                    human_delay("form")
                    continue
        except Exception as exc:
            logger.debug("[Codex][Browser] 密码页一次性验证码入口探测失败：%s", str(exc)[:140])
        time.sleep(0.5)
    if _is_login_password_page(driver):
        raise RuntimeError(
            "codex_password_step_stalled: 邮箱提交后仍停留 /log-in/password，未能进入密码提交或邮箱 OTP 页面"
        )
    if clicked:
        logger.info("[Codex][Browser] 已点击一次性验证码入口，未立即检测到 OTP 页，继续后续 OTP 轮询")


def _codex_auth_error_state(driver) -> dict:
    """识别登录页错误/资源加载失败，避免把错误页当作 OTP 页长时间等待。"""
    try:
        state = driver.execute_script("""
        return {url: location.href, text: (document.body && document.body.innerText || '').slice(0, 900)};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, "current_url", ""), "text": "", "error": f"{type(exc).__name__}: {exc}"}
    url = str(state.get("url") or "").lower()
    text = str(state.get("text") or "").lower()
    hit = (
        "oops, an error occurred" in text
        or "failed to fetch dynamically imported module" in text
        or "chrome-error" in url
        or "/auth/error" in url
        or ("/log-in" in url and not _is_email_verification_page(driver) and "try again" in text)
    )
    return {"hit": hit, "url": url, "text": text[:240]}


def _wait_for_otp_input(driver, timeout: int = 30) -> None:
    """验证码已收到但 OTP 输入框可能尚未出现（点完一次性验证码后常有中间页/延迟渲染）。

    等待期间若仍停留在登录密码页，则补点一次性验证码入口（最多 2 次、间隔 6s）；
    超时仍未出现时打印页面状态便于定位。
    """
    end = time.time() + timeout
    passwordless_retries = 0
    while time.time() < end:
        if _is_email_verification_page(driver):
            return
        if _is_login_password_page(driver) and passwordless_retries < 2:
            passwordless_retries += 1
            result = _click_passwordless_signup_if_present(driver)
            if result.get("ok"):
                logger.info("[Codex][Browser] 仍停留登录密码页，补点一次性验证码入口：%s", result.get("reason"))
                human_delay("form")
            time.sleep(6)
            continue
        time.sleep(0.8)
    state = _email_otp_page_state(driver)
    logger.warning(
        "[Codex][Browser] 等待 OTP 输入框超时，页面 url=%s inputs=%s buttons=%s 文本前300字=%s",
        str(state.get("url") or ""),
        len(state.get("inputs") or []),
        [(b.get("text") or "")[:24] for b in (state.get("buttons") or [])][:8],
        str(state.get("text") or "")[:300],
    )
    raise RuntimeError("等待 OTP 输入框超时，页面未出现验证码输入框")


# 注册流程刚设好的密码只存在于内存里：Cloak/Roxy 注册会在 Codex 授权完成后
# 才把账号写入数据库，而 Codex 登录密码页需要通过邮箱查库取密码。查库必然为空，
# 于是密码登录被静默跳过、降级为一次性验证码登录，服务端会连续报 Incorrect code
# 并最终封掉 max_check_attempts。这里保存注册阶段刚生成的密码，供查库失败时兜底。
_REGISTRATION_PASSWORD_CACHE: dict[str, str] = {}


def remember_registration_password(email: str, password: str | None) -> None:
    """注册阶段把刚设置的密码写入进程内缓存，供随后的 Codex 授权使用。"""
    target = str(email or "").strip().lower()
    secret = str(password or "").strip()
    if target and secret:
        _REGISTRATION_PASSWORD_CACHE[target] = secret


def _account_password_for_email(email: str) -> str:
    key = str(email or "").strip().lower()
    cached = _REGISTRATION_PASSWORD_CACHE.get(key, "")
    if cached:
        return cached
    try:
        stored = _codex_proto._account_registration_password(email)
    except Exception:
        stored = ""
    if stored:
        return stored
    try:
        from core import db
        return db.get_pool_password(email) or ""
    except Exception:
        return ""


def _account_totp_code_for_email(email: str) -> str:
    try:
        return _codex_proto._account_totp_code(email)
    except Exception:
        return ""


def _is_mfa_challenge_page(driver) -> bool:
    try:
        url = str(driver.current_url or "").lower()
        if "/mfa-challenge/" in url or "/mfa-challenge" in url:
            return True
        state = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const form = [...document.querySelectorAll('form')].find(f => /\/mfa-challenge/i.test(f.getAttribute('action') || ''));
        const input = form ? [...form.querySelectorAll('input[name="code"], input[autocomplete="one-time-code"], input[maxlength="6"]')].find(visible) : null;
        return {ok: !!(form && input), url: location.href};
        """) or {}
        return bool(state.get("ok"))
    except Exception:
        return False


def _fill_mfa_challenge_if_present(driver, email: str, timeout: int = 15) -> bool:
    """如果当前进入 MFA challenge 页面，自动填入账号 TOTP 并提交。"""
    code = _account_totp_code_for_email(email)
    if not code:
        return False
    end = time.time() + timeout
    while time.time() < end:
        try:
            if not _is_mfa_challenge_page(driver):
                time.sleep(0.4)
                continue
            result = driver.execute_script(r"""
            const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
              && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
              && !el.disabled && !el.readOnly;
            const form = [...document.querySelectorAll('form')].find(f => /\/mfa-challenge/i.test(f.getAttribute('action') || ''));
            if (!form) return {ok:false, reason:'missing_form'};
            const input = [...form.querySelectorAll('input[name="code"], input[autocomplete="one-time-code"], input[maxlength="6"]')].find(visible);
            if (!input) return {ok:false, reason:'missing_code_input'};
            const button = [...form.querySelectorAll('button[type="submit"], button[data-dd-action-name="Continue"], button')].find(visible);
            if (!button) return {ok:false, reason:'missing_submit'};
            return {ok:true, input, button};
            """) or {}
            if not result.get("ok"):
                time.sleep(0.4)
                continue
            _human_type_text(driver, result.get("input"), code, clear=True)
            human_delay("otp_input")
            _human_click(driver, result.get("button"), label="codex_mfa_submit")
            logger.info("[Codex][Browser] 已填写并提交 MFA 验证码：%s", email)
            wait_end = time.time() + 12
            while time.time() < wait_end:
                if not _is_mfa_challenge_page(driver):
                    return True
                time.sleep(0.4)
            return True
        except Exception as exc:
            logger.debug("[Codex][Browser] MFA challenge 处理失败：%s", str(exc)[:160])
            time.sleep(0.5)
    return False


def _human_type_password_by_selector(driver, password: str) -> None:
    """在登录密码页定位密码框并逐字符输入。

    参考实现直接使用 execute_script 返回的 input/button 元素句柄，但 Cloak 适配层
    无法把对象里的元素句柄序列化回来（handle.json_value() 遇到元素会抛错），
    因此这里按选择器重新定位元素，再交给 _human_type_text 输入。
    """
    for selector in (
        "input[type='password']",
        "input[name*='password']",
        "input[autocomplete='current-password']",
    ):
        try:
            elements = driver.find_elements("css selector", selector)
        except Exception as exc:
            logger.debug(
                "%s 密码输入框定位失败 selector=%s：%s: %s",
                _codex_prefix(), selector, type(exc).__name__, exc,
            )
            continue
        for element in elements:
            if _visible(element):
                _human_type_text(driver, element, password, clear=True)
                return
    raise RuntimeError("missing_password_input: 登录密码页未找到可输入的密码框")


def _fill_login_password_if_present(
    driver,
    email: str,
    timeout: int = 18,
    registration_password: str | None = None,
) -> str | None:
    """Codex OAuth 登录密码页优先使用本次注册密码，再回退账号存储。"""
    password = str(registration_password or "").strip() or _account_password_for_email(email)
    if not password:
        return None
    end = time.time() + timeout
    while time.time() < end:
        if _is_email_verification_page(driver):
            return "email_otp"
        if not _is_login_password_page(driver):
            time.sleep(0.4)
            continue
        result = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="password"],input[name*="password" i],input[autocomplete="current-password"]')]
          .find(visible);
        if (!input) return {ok:false, reason:'missing_password_input'};
        const form = input.closest('form');
        const scope = form || document;
        const buttons = [...scope.querySelectorAll('button,input[type="submit"]')]
          .filter(el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true')
          .map((el, idx) => {
            const r = el.getBoundingClientRect();
            const ir = input.getBoundingClientRect();
            return {el, idx, below: r.top >= ir.bottom - 10, dist: Math.max(0, r.top - ir.bottom) + Math.abs((r.left+r.right-ir.left-ir.right)/2)/10};
          })
          .filter(x => x.below)
          .sort((a,b) => a.dist - b.dist || a.idx - b.idx);
        if (!buttons.length) return {ok:false, reason:'missing_submit'};
        buttons[0].el.scrollIntoView({block:'center'});
        return {ok:true, reason:'password_targets', input, button: buttons[0].el};
        """) or {}
        if not result.get("ok"):
            logger.info("[Codex][Browser] 登录密码页未找到输入/提交按钮：%s", result)
            time.sleep(0.5)
            continue
        _human_type_text(driver, result.get("input"), password, clear=True)
        human_delay("form", minimum=2.0, maximum=3.6)
        _human_click(driver, result.get("button"), label="codex_password_submit")
        logger.info("[Codex][Browser] 已填写并提交登录密码：%s", email)
        wait_end = time.time() + 12
        while time.time() < wait_end:
            if _is_mfa_challenge_page(driver):
                _fill_mfa_challenge_if_present(driver, email, timeout=15)
                return "next_step"
            if _is_email_verification_page(driver):
                return "email_otp"
            if not _is_login_password_page(driver):
                return "next_step"
            time.sleep(0.5)
        if _is_login_password_page(driver):
            passwordless = _click_passwordless_signup_if_present(driver)
            if passwordless.get("ok"):
                logger.info(
                    "[Codex][Browser] 登录密码提交未离开密码页，改用一次性验证码：%s",
                    passwordless,
                )
                return "email_otp"
            raise RuntimeError(
                "codex_password_step_stalled: 登录密码提交后仍停留在密码页 "
                f"url={str(getattr(driver, 'current_url', '') or '')[:240]} "
                f"passwordless={passwordless}"
            )
        return "next_step"
    return None


def _wait_for_codex_auth_entry_state(driver, timeout: int = 12) -> str:
    """等待邮箱提交后的 password、MFA 或邮箱 OTP 页面完成渲染。"""
    end = time.time() + timeout
    while time.time() < end:
        if _is_mfa_challenge_page(driver):
            return "mfa"
        if _is_email_verification_page(driver):
            return "email_otp"
        if _is_login_password_page(driver):
            return "password"
        _stop_sleep(0.4)
    try:
        state = driver.execute_script("return {url:location.href, title:document.title, text:(document.body?.innerText || '').trim().slice(0,500)};") or {}
        logger.warning("[Codex][Browser] 邮箱提交后认证页面仍未就绪：%s", state)
    except Exception as exc:
        logger.warning("[Codex][Browser] 邮箱提交后页面诊断失败：%s: %s", type(exc).__name__, str(exc)[:160])
    return "unknown"


def _fill_email_and_otp(
    driver,
    email: str,
    otp_provider,
    auth_url: str,
    registration_password: str | None = None,
) -> None:
    otp_after_ts = time.time()

    def _recover_email_submit_stall_once() -> str:
        """重开一次 CPA authorize，恢复卡在 Welcome back 的邮箱提交。"""
        nonlocal otp_after_ts
        logger.warning(
            "[Codex][Browser] 邮箱提交停在登录页，重开 authorize 并完整重提一次：url=%s",
            str(auth_url)[:180],
        )
        driver.get(auth_url)
        human_delay("navigate")
        _maybe_accept(driver)
        _type_email_address(driver, email, timeout=12)
        human_delay("form")
        _submit_email_step(driver)
        logger.info("[Codex][Browser] authorize 恢复已重新提交邮箱")

        recovered_password_state = _fill_login_password_if_present(
            driver, email, timeout=18, registration_password=registration_password
        )
        if recovered_password_state in ("next_step", "email_otp"):
            if recovered_password_state == "email_otp":
                otp_after_ts = time.time()
            return recovered_password_state
        if _is_mfa_challenge_page(driver):
            _fill_mfa_challenge_if_present(driver, email, timeout=15)
            return "mfa"
        if _is_email_verification_page(driver):
            otp_after_ts = time.time()
            return "email_otp"
        _maybe_click_passwordless_after_email(driver, email, timeout=18)
        entry_state = _wait_for_codex_auth_entry_state(driver, timeout=24)
        if entry_state == "email_otp":
            otp_after_ts = time.time()
        return entry_state

    logger.info("[Codex][Browser] 打开授权地址")
    logger.info("[Codex][Browser] 完整授权地址: %s", auth_url)
    driver.get(auth_url)
    human_delay("navigate")
    logger.info("[Codex][Browser] 授权页加载完成，检查是否需要邮箱登录")
    _maybe_accept(driver)

    # 可能已经处于账号选择/授权页；如果有邮箱输入框则完整登录。
    # 非日本出口时按钮文案/顺序会变，不能按可见文字点“继续”，否则可能误点 Google。
    try:
        _type_email_address(driver, email, timeout=12)
        logger.info("[Codex][Browser] 已填写邮箱：%s", email)
        human_delay("form")
        _submit_email_step(driver)
        logger.info("[Codex][Browser] 已提交邮箱，等待邮箱 OTP 页面")
        pw_result = _fill_login_password_if_present(
            driver, email, timeout=18, registration_password=registration_password
        )
    except Exception as exc:
        message = str(exc)
        if "codex_password_step_stalled" in message:
            raise
        if "email_submit_stalled" in message:
            if _is_login_password_page(driver):
                logger.warning(
                    "[Codex][Browser] 邮箱提交等待结束时已到达登录密码页，继续处理密码步骤：%s",
                    message[:180],
                )
                pw_result = _fill_login_password_if_present(
                    driver, email, timeout=18, registration_password=registration_password
                )
            elif _is_email_verification_page(driver):
                logger.info(
                    "[Codex][Browser] 邮箱提交阶段报告异常，但页面已到达邮箱 OTP，继续验证码流程：%s",
                    message[:160],
                )
                pw_result = "email_otp"
            else:
                # /log-in 可能只是提交后的短暂过渡态；不能把它当成“已登录”，
                # 否则页面稍后进入 /log-in/password 时会直接落入 callback 超时。
                entry_state = _wait_for_codex_auth_entry_state(driver, timeout=24)
                if entry_state == "password":
                    logger.warning(
                        "[Codex][Browser] 邮箱提交后延迟进入登录密码页，继续处理密码步骤：%s",
                        message[:180],
                    )
                    pw_result = _fill_login_password_if_present(
                        driver, email, timeout=18, registration_password=registration_password
                    )
                elif entry_state == "email_otp":
                    logger.info("[Codex][Browser] 邮箱提交后延迟进入邮箱 OTP 页面")
                    pw_result = "email_otp"
                elif entry_state == "mfa":
                    _fill_mfa_challenge_if_present(driver, email, timeout=15)
                    return
                else:
                    current_url = str(getattr(driver, "current_url", "") or "")
                    lower_url = current_url.lower()
                    if any(marker in lower_url for marker in (
                        "/oauth/authorize", "/consent", "/workspace", "/phone", "localhost:1455",
                    )):
                        logger.info(
                            "[Codex][Browser] 邮箱提交异常后已进入后续授权状态，继续 callback：url=%s",
                            current_url[:180],
                        )
                        return
                    try:
                        recovered_state = _recover_email_submit_stall_once()
                    except Exception as recovery_exc:
                        raise RuntimeError(
                            f"codex_email_submit_stalled: authorize 恢复后仍未进入密码/OTP步骤 "
                            f"url={current_url or 'unknown'} recovery={str(recovery_exc)[:180]}"
                        ) from recovery_exc
                    if recovered_state == "password":
                        pw_result = _fill_login_password_if_present(
                            driver, email, timeout=18, registration_password=registration_password
                        )
                    elif recovered_state == "email_otp":
                        logger.info("[Codex][Browser] authorize 恢复后进入邮箱 OTP 页面")
                        pw_result = "email_otp"
                    elif recovered_state == "mfa":
                        return
                    elif recovered_state == "next_step":
                        return
                    else:
                        current_url = str(getattr(driver, "current_url", "") or "")
                        lower_url = current_url.lower()
                        if any(marker in lower_url for marker in (
                            "/oauth/authorize", "/consent", "/workspace", "/phone", "localhost:1455",
                        )):
                            logger.info(
                                "[Codex][Browser] authorize 恢复后已进入后续授权状态，继续 callback：url=%s",
                                current_url[:180],
                            )
                            return
                        raise RuntimeError(
                            f"codex_email_submit_stalled: 认证页面在限定等待后仍未进入密码/OTP步骤 "
                            f"url={current_url or 'unknown'} cause={message[:180]} recovery_state={recovered_state}"
                        ) from exc
        elif _is_email_verification_page(driver):
            logger.info(
                "[Codex][Browser] 邮箱提交阶段报告异常，但页面已到达邮箱 OTP，继续验证码流程：%s",
                message[:160],
            )
            pw_result = "email_otp"
        else:
            logger.info("[Codex][Browser] 未检测到邮箱输入框，可能已登录或进入下一步：%s", message[:120])
            return

    if pw_result == "email_otp":
        # 密码登录可能刚刚触发一封新的 OTP；不要拿 authorize 初始时间
        # 去筛掉这封邮件。
        otp_after_ts = time.time()
    if pw_result == "next_step":
        if _is_mfa_challenge_page(driver):
            _fill_mfa_challenge_if_present(driver, email, timeout=15)
        logger.info("[Codex][Browser] 账号已用密码完成登录，直接进入后续步骤")
        return
    if pw_result == "email_otp":
        logger.info("[Codex][Browser] 密码登录后仍进入邮箱 OTP 页面")
    else:
        _maybe_click_passwordless_after_email(driver, email, timeout=18)

    # 提交邮箱后不再执行任何全局“继续/授权/分支”兜底点击；后续只等待验证码页。
    # 避免页面已进入 OAuth consent 时误点授权按钮。

    used_codes: set[str] = set()
    max_otp_attempts = 3

    def _restart_email_otp_flow(reason: str) -> None:
        """Codex Auth 上直接点 resend 可能触发服务端 500；这里改为重新打开授权地址并提交邮箱。"""
        nonlocal otp_after_ts
        logger.info("[Codex][Browser] 重新触发邮箱 OTP：%s", reason)
        otp_after_ts = time.time()
        driver.get(auth_url)
        human_delay("navigate")
        _maybe_accept(driver)
        try:
            _type_email_address(driver, email, timeout=12)
            human_delay("form")
            _submit_email_step(driver)
            logger.info("[Codex][Browser] 已重新提交邮箱触发 OTP")
            pw_result = _fill_login_password_if_present(
                driver, email, timeout=12, registration_password=registration_password
            )
            if pw_result == "next_step":
                if _is_mfa_challenge_page(driver):
                    _fill_mfa_challenge_if_present(driver, email, timeout=15)
                logger.info("[Codex][Browser] 重新提交邮箱后已用密码完成登录，进入后续步骤")
                return
            if pw_result != "email_otp":
                _maybe_click_passwordless_after_email(driver, email, timeout=12)
        except Exception as exc:
            if "codex_password_step_stalled" in str(exc):
                raise
            # 如果重进授权地址后已经停在验证码/下一步页面，就不要再强行提交。
            if not _is_email_verification_page(driver):
                logger.warning("[Codex][Browser] 重新提交邮箱失败，继续按当前页面轮询：%s", str(exc)[:180])
            else:
                logger.info("[Codex][Browser] 重开授权后已在邮箱 OTP 页面")
        human_delay("api")

    for otp_attempt in range(1, max_otp_attempts + 1):
        logger.info("[Codex][Browser] 等待邮箱 OTP：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
        try:
            code = _wait_for_fresh_email_otp(
                otp_provider,
                email,
                after_ts=otp_after_ts,
                used_codes=used_codes,
                timeout=90,
            )
        except Exception as exc:
            if otp_attempt >= max_otp_attempts:
                raise
            logger.warning(
                "[Codex][Browser] 一直未收到邮箱 OTP，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                otp_attempt + 1,
                max_otp_attempts,
                type(exc).__name__,
                str(exc)[:180],
            )
            _restart_email_otp_flow("等待验证码超时，避免点击 resend 导致 500")
            continue
        used_codes.add(str(code))
        logger.info("[Codex][Browser] 邮箱 OTP 收到：%s", code)
        _wait_for_otp_input(driver, timeout=30)
        _clear_otp_inputs(driver)
        _install_email_otp_validate_hook(driver)
        baseline_count = _email_otp_validate_snapshot(driver).get("count", 0)
        _type_otp(driver, code)
        logger.info("[Codex][Browser] 已填写邮箱 OTP")
        human_delay("otp_input")
        submitted = _wait_for_codex_auto_submit(driver, baseline_count)
        if not submitted:
            clicked = _click_if_present(driver, [
                "button[type='submit']",
                "//button[contains(., 'Continue')]",
                "//button[contains(., '继续')]",
                "//button[contains(., 'Verify')]",
                "//button[contains(., '验证')]",
            ], timeout=8)
            if clicked:
                logger.info("[Codex][Browser] 已提交邮箱 OTP，等待后续授权/手机号页面")
            else:
                logger.info("[Codex][Browser] 未找到显式提交按钮，继续等待页面状态")
        else:
            logger.info("[Codex][Browser] 页面已自动提交邮箱 OTP，不再重复点击")

        outcome = _wait_after_email_otp_submit(driver, timeout=45)
        logger.info("[Codex][Browser] 邮箱 OTP 提交后状态：%s", outcome)
        if _is_mfa_challenge_page(driver):
            _fill_mfa_challenge_if_present(driver, email, timeout=15)
            return
        if outcome == "accepted":
            return
        if str(outcome).startswith("deactivated:"):
            error_code = str(outcome).split(":", 1)[1] or "account_deactivated"
            raise AccountUnusableError(f"账号已废（{error_code}）", error_code=error_code)

        if otp_attempt >= max_otp_attempts:
            raise RuntimeError("Codex 邮箱验证码连续错误/过期，已达到最大重试次数")

        validate_snapshot = _email_otp_validate_snapshot(driver)
        logger.warning(
            "[Codex][Browser] 邮箱验证码未通过，重开授权流程获取新码（下一轮 %s/%s，"
            "已排除旧码=%s，validate=%s）",
            otp_attempt + 1,
            max_otp_attempts,
            len(used_codes),
            validate_snapshot,
        )
        _restart_email_otp_flow("验证码未通过；重开授权并排除已提交旧码，避免点击 resend 导致 500")



def _wait_for_fresh_email_otp(otp_provider, email: str, after_ts: float, used_codes: set[str] | None = None, timeout: int = 90) -> str:
    """获取一个未提交过的邮箱 OTP。

    通用 API 邮箱的取码接口有时会先返回缓存旧码；验证码错误后重发时，
    这里会拒绝复用已失败的 code，持续轮询直到出现新 code 或超时。
    """
    used_codes = {str(x) for x in (used_codes or set()) if x}
    end = time.time() + timeout
    last_code = ""
    while True:
        code = str(otp_provider(email, after_ts=after_ts) or "").strip()
        if code and code not in used_codes:
            return code
        last_code = code or last_code
        remaining = int(end - time.time())
        if remaining <= 0:
            raise RuntimeError(
                f"等待新的邮箱验证码超时，取码接口仍返回已失败验证码：length={len(last_code) if last_code else 0}"
            )
        logger.warning(
            "[Codex][Browser] 取码接口仍返回已提交过的旧 OTP，继续等待最新验证码（length=%s，剩余 %ss）",
            len(last_code) if last_code else 0,
            remaining,
        )
        _stop_sleep(min(5, max(1, remaining)))


def _install_email_otp_validate_hook(driver) -> None:
    """
    在页面内 hook fetch/XHR，捕获 email-otp/validate 的接口响应体。

    指纹浏览器不能像纯协议模式一样直接拿 requests.Response，因此在提交邮箱 OTP 前
    注入此 hook，后续只读取接口 JSON error.code，不靠页面文字判断废号。
    """
    script = r"""
    (() => {
      window.__codexEmailOtpValidateResponses = [];
      window.__codexEmailOtpValidatePending = 0;
      if (window.__codexEmailOtpValidateHooked) return true;
      window.__codexEmailOtpValidateHooked = true;
      const hit = (url) => String(url || '').includes('/api/accounts/email-otp/validate');
      const save = (url, status, body) => {
        try {
          if (!hit(url)) return;
          window.__codexEmailOtpValidateResponses.push({
            url: String(url || ''),
            status: Number(status || 0),
            body: String(body || '').slice(0, 2000),
            ts: Date.now(),
          });
        } catch (e) {}
      };
      const origFetch = window.fetch;
      if (origFetch) {
        window.fetch = function(input, init) {
          const url = (typeof input === 'string') ? input : (input && input.url);
          const tracked = hit(url);
          if (tracked) {
            window.__codexEmailOtpValidatePending = Number(window.__codexEmailOtpValidatePending || 0) + 1;
          }
          const request = origFetch.apply(this, arguments);
          if (!tracked) return request;
          return request.then(resp => {
            resp.clone().text().then(t => save(url, resp.status, t)).catch(() => {}).finally(() => {
              window.__codexEmailOtpValidatePending = Math.max(0, Number(window.__codexEmailOtpValidatePending || 1) - 1);
            });
            return resp;
          }, error => {
            window.__codexEmailOtpValidatePending = Math.max(0, Number(window.__codexEmailOtpValidatePending || 1) - 1);
            throw error;
          });
        };
      }
      const origOpen = XMLHttpRequest.prototype.open;
      const origSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(method, url) {
        this.__codexOtpValidateUrl = url;
        return origOpen.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function() {
        try {
          if (hit(this.__codexOtpValidateUrl)) {
            window.__codexEmailOtpValidatePending = Number(window.__codexEmailOtpValidatePending || 0) + 1;
          }
          this.addEventListener('loadend', function() {
            try {
              if (hit(this.__codexOtpValidateUrl)) save(this.__codexOtpValidateUrl, this.status, this.responseText);
            } catch (e) {}
            if (hit(this.__codexOtpValidateUrl)) {
              window.__codexEmailOtpValidatePending = Math.max(0, Number(window.__codexEmailOtpValidatePending || 1) - 1);
            }
          });
        } catch (e) {}
        return origSend.apply(this, arguments);
      };
      return true;
    })();
    """
    try:
        driver.execute_script(script)
    except Exception as exc:
        logger.debug("[Codex][Browser] 注入 email-otp/validate 响应 hook 失败：%s", exc)


def _email_otp_validate_snapshot(driver) -> dict:
    """读取 validate 请求的脱敏计数和响应元数据。"""
    try:
        rows = driver.execute_script("return window.__codexEmailOtpValidateResponses || [];") or []
        pending = driver.execute_script("return Number(window.__codexEmailOtpValidatePending || 0);") or 0
    except Exception:
        return {"count": 0, "pending": 0, "last": {}}
    if not isinstance(rows, list):
        rows = []
    last = rows[-1] if rows and isinstance(rows[-1], dict) else {}
    body = str(last.get("body") or "")
    json_keys: list[str] = []
    error_keys: list[str] = []
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            json_keys = sorted(str(key) for key in payload.keys())[:20]
            error = payload.get("error")
            if isinstance(error, dict):
                error_keys = sorted(str(key) for key in error.keys())[:20]
    except Exception:
        pass
    return {
        "count": len(rows),
        "pending": max(0, int(pending)),
        "last": {
            "status": int(last.get("status") or 0),
            "body_length": len(body),
            "json_keys": json_keys,
            "error_keys": error_keys,
        },
    }


def _codex_otp_input_state(driver) -> dict:
    """读取 OTP 输入框结构，只返回长度和属性，不返回验证码内容。"""
    try:
        return driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const all = [...document.querySelectorAll('input')].filter(visible);
        const isOtp = el => /one-time|otp|code|numeric|tel/.test([
          el.type, el.name, el.id, el.autocomplete, el.inputMode, el.getAttribute('aria-label') || ''
        ].join(' ').toLowerCase());
        const inputs = all.slice(0, 40).map((el, index) => ({
          index,
          otp: isOtp(el),
          type: el.getAttribute('type') || '',
          name: el.getAttribute('name') || '',
          id: el.id || '',
          autocomplete: el.getAttribute('autocomplete') || '',
          inputmode: el.getAttribute('inputmode') || '',
          maxlength: Number.isFinite(el.maxLength) ? el.maxLength : null,
          value_length: String(el.value || '').length,
          aria_invalid: el.getAttribute('aria-invalid') || '',
          disabled: !!el.disabled,
          focused: document.activeElement === el,
        }));
        const forms = [...new Set(all.map(el => el.closest('form')).filter(Boolean))].slice(0, 10).map(form => ({
          action: (() => { try { return new URL(form.getAttribute('action') || location.href, location.href).pathname; } catch (_) { return ''; } })(),
          method: form.getAttribute('method') || '',
          submit_count: [...form.querySelectorAll('button[type="submit"],input[type="submit"],button')].filter(visible).length,
        }));
        const buttons = [...document.querySelectorAll('button[type="submit"],input[type="submit"],button')]
          .filter(visible).slice(0, 20).map(el => ({
            type: el.getAttribute('type') || '',
            action: el.getAttribute('data-dd-action-name') || '',
            disabled: !!el.disabled || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true',
          }));
        return {url: location.href, visible_input_count: all.length, inputs, forms, buttons};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, "current_url", ""), "error": f"{type(exc).__name__}: {exc}"}


def _codex_otp_state_summary(state: dict) -> dict:
    """将页面输入状态压缩成可安全写入日志的摘要。"""
    if not isinstance(state, dict):
        return {"state": "invalid"}
    url = str(state.get("url") or "")
    try:
        url = urlparse(url).path
    except Exception:
        url = ""
    return {
        "url": url,
        "visible_input_count": state.get("visible_input_count", 0),
        "otp_inputs": [
            {
                key: item.get(key)
                for key in ("index", "type", "name", "id", "autocomplete", "inputmode", "maxlength", "value_length", "aria_invalid", "disabled", "focused")
            }
            for item in (state.get("inputs") or [])
            if isinstance(item, dict) and item.get("otp")
        ],
        "forms": state.get("forms") or [],
        "buttons": state.get("buttons") or [],
        "error": state.get("error", ""),
    }


def _codex_otp_input_complete(state: dict, code: str) -> bool:
    if not isinstance(state, dict) or state.get("error"):
        return False
    expected = len(str(code or ""))
    otp_inputs = [item for item in (state.get("inputs") or []) if isinstance(item, dict) and item.get("otp")]
    lengths = [int(item.get("value_length") or 0) for item in otp_inputs]
    if len(otp_inputs) == 1:
        return lengths[0] == expected
    return bool(otp_inputs) and sum(lengths) == expected and all(length == 1 for length in lengths[:expected])


def _set_codex_otp_dom_value(driver, code: str) -> dict:
    """用原生 value setter 同步 React OTP 状态，作为键盘输入失败的兜底。"""
    try:
        return driver.execute_script(r"""
        const code = String(arguments[0] || '');
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const isOtp = el => /one-time|otp|code|numeric|tel/.test([
          el.type, el.name, el.id, el.autocomplete, el.inputMode, el.getAttribute('aria-label') || ''
        ].join(' ').toLowerCase());
        const inputs = [...document.querySelectorAll('input')].filter(el => visible(el) && isOtp(el));
        if (!inputs.length) return {ok:false, reason:'missing_otp_inputs'};
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        const emit = (el, value) => {
          if (setter) setter.call(el, value); else el.value = value;
          try { el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:value})); }
          catch (_) { el.dispatchEvent(new Event('input', {bubbles:true})); }
          el.dispatchEvent(new Event('change', {bubbles:true}));
        };
        const aggregate = inputs.find(el => Number(el.maxLength) >= code.length || el.name === 'code' || el.id === 'code');
        if (inputs.length === 1 || (aggregate && Number(aggregate.maxLength || -1) !== 1)) {
          const target = aggregate || inputs[0];
          emit(target, code);
          target.focus();
          return {ok:true, mode:'single', count:inputs.length, lengths:inputs.map(el => String(el.value || '').length)};
        }
        const boxes = inputs.slice(0, code.length);
        if (boxes.length < code.length) return {ok:false, reason:'insufficient_otp_inputs', count:boxes.length};
        boxes.forEach((el, index) => emit(el, code[index] || ''));
        boxes[boxes.length - 1].focus();
        return {ok:true, mode:'multi', count:boxes.length, lengths:boxes.map(el => String(el.value || '').length)};
        """, str(code or "")) or {}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _codex_otp_input_matches(driver, code: str) -> bool:
    """Compare the rendered OTP fields with code without exposing the code in logs."""
    try:
        return bool(driver.execute_script(r"""
        const expected = String(arguments[0] || '');
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const isOtp = el => /one-time|otp|code|numeric|tel/.test([
          el.type, el.name, el.id, el.autocomplete, el.inputMode, el.getAttribute('aria-label') || ''
        ].join(' ').toLowerCase());
        const values = [...document.querySelectorAll('input')]
          .filter(el => visible(el) && isOtp(el))
          .map(el => String(el.value || ''));
        if (!values.length) return false;
        return values.length === 1 ? values[0] === expected : values.join('') === expected;
        """, str(code or '')))
    except Exception:
        return False


def _fill_phone_otp(driver, code: str) -> dict:
    """Fill phone OTP through native Cloak Page.fill and verify the rendered value."""
    from selenium.webdriver.common.by import By

    normalized_code = str(code or "").strip()
    if not normalized_code or not normalized_code.isdigit():
        raise RuntimeError("phone_otp_input_sync_failed: 收到空或非数字手机验证码")
    _clear_otp_inputs(driver)
    selectors = [
        "input[autocomplete='one-time-code']",
        "input[name='code']",
        "input[inputmode='numeric']",
        "input[type='tel']",
    ]
    selected = []
    mode = ""
    input_deadline = time.time() + 8
    while time.time() < input_deadline and not selected:
        for selector in selectors:
            try:
                candidates = [e for e in driver.find_elements(By.CSS_SELECTOR, selector) if _visible(e)]
            except Exception:
                candidates = []
            if len(candidates) == 1:
                selected = candidates
                mode = "single"
                break
        if selected:
            break
        try:
            inputs = [e for e in driver.find_elements(By.CSS_SELECTOR, "input") if _visible(e)]
        except Exception:
            inputs = []
        numeric = []
        for element in inputs:
            attrs = " ".join(
                str(element.get_attribute(key) or "")
                for key in ("inputmode", "autocomplete", "aria-label", "name", "id", "type")
            ).lower()
            if any(marker in attrs for marker in ("numeric", "one-time", "code", "otp", "tel")):
                numeric.append(element)
        if len(numeric) >= len(normalized_code):
            selected = numeric[:len(normalized_code)]
            mode = "multi"
            break
        _stop_sleep(0.2)
    if not selected:
        raise RuntimeError(
            f"phone_otp_input_sync_failed: 找不到手机 OTP 输入框 state={_codex_otp_state_summary(_codex_otp_input_state(driver))}"
        )

    try:
        if mode == "single":
            fill = getattr(selected[0], "fill", None)
            if callable(fill):
                fill(normalized_code, timeout=5000)
            else:
                _human_type_text(driver, selected[0], normalized_code, clear=True)
        else:
            for element, digit in zip(selected, normalized_code):
                fill = getattr(element, "fill", None)
                if callable(fill):
                    fill(digit, timeout=5000)
                else:
                    _human_type_text(driver, element, digit, clear=True)
    except Exception as exc:
        logger.debug("[Codex][Browser] 手机 OTP 原子填值失败，转 native setter：%s", str(exc)[:160])

    _stop_sleep(0.25)
    validate_state = _phone_otp_validate_snapshot(driver)
    if validate_state.get("count", 0) > 0 or validate_state.get("pending", 0) > 0:
        return {
            "mode": mode,
            "input_count": len(selected),
            "auto_submitted": True,
            "state": _codex_otp_state_summary(_codex_otp_input_state(driver)),
        }
    if not _is_phone_code_page(driver):
        return {
            "mode": mode,
            "input_count": len(selected),
            "auto_submitted": True,
            "state": _codex_otp_state_summary(_codex_otp_input_state(driver)),
        }
    state = _codex_otp_input_state(driver)
    matched = _codex_otp_input_matches(driver, normalized_code)
    if not matched:
        fallback = _set_codex_otp_dom_value(driver, normalized_code)
        _stop_sleep(0.25)
        state = _codex_otp_input_state(driver)
        matched = bool(fallback.get("ok")) and _codex_otp_input_matches(driver, normalized_code)
    if not matched or not _codex_otp_input_complete(state, normalized_code):
        raise RuntimeError(
            f"phone_otp_input_sync_failed: 手机 OTP 实际输入值未与验证码匹配 "
            f"mode={mode} state={_codex_otp_state_summary(state)}"
        )
    return {
        "mode": mode,
        "input_count": len(selected),
        "state": _codex_otp_state_summary(state),
    }


def _install_phone_otp_validate_hook(driver) -> None:
    """Track phone OTP validation requests without recording code or body."""
    try:
        driver.execute_script(r"""
        (() => {
          window.__codexPhoneOtpValidateCount = 0;
          window.__codexPhoneOtpValidatePending = 0;
          if (window.__codexPhoneOtpValidateHooked) return true;
          window.__codexPhoneOtpValidateHooked = true;
          const hit = url => String(url || '').includes('/api/accounts/phone-otp/validate');
          const started = () => {
            window.__codexPhoneOtpValidateCount = Number(window.__codexPhoneOtpValidateCount || 0) + 1;
            window.__codexPhoneOtpValidatePending = Number(window.__codexPhoneOtpValidatePending || 0) + 1;
          };
          const finished = () => {
            window.__codexPhoneOtpValidatePending = Math.max(0, Number(window.__codexPhoneOtpValidatePending || 1) - 1);
          };
          const origFetch = window.fetch;
          if (origFetch) {
            window.fetch = function(input) {
              const url = (typeof input === 'string') ? input : (input && input.url);
              const tracked = hit(url);
              if (tracked) started();
              const request = origFetch.apply(this, arguments);
              if (tracked) request.then(finished, finished);
              return request;
            };
          }
          const origOpen = XMLHttpRequest.prototype.open;
          const origSend = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function(method, url) {
            this.__codexPhoneOtpValidateUrl = url;
            return origOpen.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function() {
            const tracked = hit(this.__codexPhoneOtpValidateUrl);
            if (tracked) {
              started();
              this.addEventListener('loadend', finished, {once:true});
            }
            return origSend.apply(this, arguments);
          };
          return true;
        })();
        """)
    except Exception as exc:
        logger.debug("[Codex][Browser] 注入 phone-otp/validate 请求 hook 失败：%s", str(exc)[:160])


def _phone_otp_validate_snapshot(driver) -> dict:
    try:
        return {
            "count": int(driver.execute_script("return Number(window.__codexPhoneOtpValidateCount || 0);") or 0),
            "pending": int(driver.execute_script("return Number(window.__codexPhoneOtpValidatePending || 0);") or 0),
        }
    except Exception:
        return {"count": 0, "pending": 0}


def _phone_otp_auto_submit_started(driver, baseline_count: int) -> bool:
    snapshot = _phone_otp_validate_snapshot(driver)
    if snapshot.get("count", 0) > int(baseline_count or 0) or snapshot.get("pending", 0) > 0:
        return True
    return not _is_phone_code_page(driver)


def _wait_for_phone_otp_auto_submit(driver, baseline_count: int, timeout: float = 2.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _phone_otp_auto_submit_started(driver, baseline_count):
            return True
        _stop_sleep(0.2)
    return _phone_otp_auto_submit_started(driver, baseline_count)


def _codex_auto_submit_started(driver, baseline_count: int) -> bool:
    snapshot = _email_otp_validate_snapshot(driver)
    if snapshot.get("count", 0) > int(baseline_count or 0) or snapshot.get("pending", 0) > 0:
        return True
    try:
        url = str(driver.current_url or "").lower()
    except Exception:
        url = ""
    return bool(url and "email-verification" not in url and not _is_email_verification_page(driver))


def _wait_for_codex_auto_submit(driver, baseline_count: int, timeout: float = 2.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _codex_auto_submit_started(driver, baseline_count):
            return True
        _stop_sleep(0.2)
    return _codex_auto_submit_started(driver, baseline_count)


def _read_email_otp_validate_dead_code(driver) -> str:
    try:
        rows = driver.execute_script("return window.__codexEmailOtpValidateResponses || [];") or []
    except Exception:
        return ""
    if not isinstance(rows, list):
        return ""
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        code = detect_account_unusable_response_body(str(row.get("body") or ""))
        if code:
            logger.warning(
                "[Codex][Browser] email-otp/validate 响应识别账号已废：code=%s status=%s",
                code,
                row.get("status"),
            )
            return code
    return ""


# 邮箱验证码页判断复用 roxy_registration 的强版本（URL + 输入框属性识别，
# 且明确排除 /log-in/password），不使用本地弱化版，避免点完一次性验证码后
# 页面已渲染 OTP 输入框却因 URL 不含 email-verification 而识别失败。

def _wait_after_email_otp_submit(driver, timeout: int = 45) -> str:
    """
    提交邮箱 OTP 后等待页面离开 /email-verification。

    返回：
      - accepted：已离开邮箱验证码页 / 进入手机号页 / 进入 callback；
      - invalid：页面明确报错、输入框标红，或长时间停留验证码页。
    """
    end = time.time() + timeout
    last_url = ""
    last_log = 0.0
    while time.time() < end:
        try:
            dead_code = _read_email_otp_validate_dead_code(driver)
            if dead_code:
                return f"deactivated:{dead_code}"
            url = str(driver.current_url or "")
            if url != last_url:
                logger.info("[Codex][Browser] 邮箱 OTP 后等待跳转：url=%s", url)
                last_url = url
            if _is_callback_url(url):
                return "accepted"
            if _has_strict_add_phone_form(driver) or _is_phone_code_page(driver):
                return "accepted"
            # 已经离开 email-verification，交给后续授权/手机号/consent 流程处理。
            if "email-verification" not in url.lower():
                return "accepted"

            state = _email_otp_page_state(driver)
            invalid = any(str(i.get("ariaInvalid") or "").lower() == "true" for i in (state.get("inputs") or []))
            errors = [str(x) for x in (state.get("errors") or []) if str(x).strip()]
            body_text = str(state.get("text") or "").lower()
            dead_text_code = detect_account_unusable_text(body_text)
            if dead_text_code:
                logger.warning(
                    "[Codex][Browser] 邮箱 OTP 页面文案识别账号已废：code=%s",
                    dead_text_code,
                )
                return f"deactivated:{dead_text_code}"
            error_hit = any(x in body_text for x in (
                "invalid code", "incorrect code", "wrong code", "expired",
                "验证码错误", "验证码无效", "验证码已过期", "コードが正しく", "無効", "期限",
            ))
            if invalid or errors or error_hit:
                logger.warning(
                    "[Codex][Browser] 邮箱 OTP 提交后检测到错误/仍需验证码：errors=%s invalid=%s url=%s",
                    errors[:3],
                    invalid,
                    url,
                )
                return "invalid"

            if time.time() - last_log > 6:
                logger.info("[Codex][Browser] 邮箱 OTP 后仍在 email-verification，继续等待页面自动跳转")
                last_log = time.time()
        except Exception:
            pass
        _stop_sleep(0.5)
    logger.warning("[Codex][Browser] 邮箱 OTP 后等待跳转超时，当前 url=%s，按验证码无效/过期处理", getattr(driver, "current_url", ""))
    return "invalid"


def _phone_page_state(driver) -> dict:
    try:
        return driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const radios = [...document.querySelectorAll('input[type=radio]')].map(el => ({
          name: el.name || '', value: el.value || '', checked: !!el.checked, id: el.id || '', visible:visible(el)
        }));
        const inputs = [...document.querySelectorAll('input,select,textarea')].filter(visible).map(el => ({
          tag: el.tagName, type: el.getAttribute('type') || '', name: el.getAttribute('name') || '',
          id: el.id || '', autocomplete: el.getAttribute('autocomplete') || '', placeholder: el.getAttribute('placeholder') || '',
          ariaInvalid: el.getAttribute('aria-invalid') || '', value: el.value || ''
        }));
        const forms = [...document.querySelectorAll('form')].map(f => ({action: f.getAttribute('action') || ''}));
        const bodyText = (document.body?.innerText || '').slice(0, 1200);
        return {url: location.href, radios, inputs, forms, bodyText};
        """) or {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "url": getattr(driver, 'current_url', '')}


def _assert_sms_channel_or_raise(driver) -> None:
    state = _phone_page_state(driver)
    radios = state.get("radios") or []
    normalized = [str(item.get("value") or "").lower().replace(" ", "") for item in radios]
    has_sms = any(value in ("sms", "text", "text_message", "text-message") for value in normalized)
    has_whatsapp = any("whatsapp" in value for value in normalized)
    sms_checked = any(
        value in ("sms", "text", "text_message", "text-message") and bool(item.get("checked"))
        for value, item in zip(normalized, radios)
    )
    whatsapp_checked = any(
        "whatsapp" in value and bool(item.get("checked"))
        for value, item in zip(normalized, radios)
    )
    if whatsapp_checked:
        marker = "whatsapp_channel_reverted" if has_sms else "whatsapp_channel"
        raise RuntimeError(f"{marker}: SMS 通道未保持选中 state={state}")
    if has_sms and not sms_checked:
        raise RuntimeError(f"sms_channel_select_failed: SMS 通道未被选中 state={state}")
    if has_whatsapp and not has_sms:
        raise RuntimeError(f"whatsapp_channel: 页面仅提供 WhatsApp 通道 state={state}")


def _select_sms_channel_or_raise(driver) -> None:
    state = _phone_page_state(driver)
    radios = state.get("radios") or []
    # 如果存在 WhatsApp 且没有 SMS/text 可选，当前接码平台无法读取 WhatsApp，直接换号。
    has_whatsapp = any("whatsapp" in str(r.get("value", "")).lower().replace(" ", "") for r in radios)
    has_sms = any(
        str(r.get("value", "")).lower().replace(" ", "") in ("sms", "text", "text_message", "text-message")
        for r in radios
    )
    if has_whatsapp and not has_sms:
        raise RuntimeError(f"whatsapp_channel: 页面仅提供 WhatsApp 通道 state={state}")
    # 没有可确认的 SMS 选项时，不能把默认通道猜成 SMS。
    if not has_sms:
        if not has_whatsapp:
            return
        raise RuntimeError(f"sms_channel_select_failed: 页面未找到 SMS 通道 state={state}")

    # React/React-Aria 可能在失焦或异步校验后重渲染表单。选择后重新读取
    # checked 状态，提交前若回到 WhatsApp 就立即释放当前激活并换号。
    last_state = state
    for selection_round in range(1, 4):
        target_info = driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const radios = [...document.querySelectorAll('input[type=radio]')];
        const sms = radios.find(el => /^(sms|text|text_message|text-message)$/i.test(el.value || ''));
        if (!sms) return null;
        const label = sms.id ? [...document.querySelectorAll('label')].find(el => el.htmlFor === sms.id) : null;
        const candidates = [label, sms.closest('label'), sms.closest('[role="radio"]'), sms].filter(Boolean);
        return {radio:sms, target:candidates.find(visible) || sms};
        """) or {}
        radio = target_info.get("radio")
        target = target_info.get("target") or radio
        if not target:
            logger.warning("[Codex][Browser] 当前页面找不到可点击的 SMS 通道控件")
            continue
        try:
            _human_click(driver, target, label="codex_sms_channel")
        except Exception as click_exc:
            logger.warning("[Codex][Browser] SMS 真实控件点击失败：%s", str(click_exc)[:160])
            continue
        # 只读取真实点击后的 React 状态，不再用 JS click/input/change 二次改写。
        _stop_sleep(0.8)
        last_state = _phone_page_state(driver)
        last_radios = last_state.get("radios") or []
        sms_checked = any(
            str(item.get("value") or "").lower().replace(" ", "") in ("sms", "text", "text_message", "text-message")
            and bool(item.get("checked"))
            for item in last_radios
        )
        whatsapp_checked = any(
            "whatsapp" in str(item.get("value") or "").lower().replace(" ", "")
            and bool(item.get("checked"))
            for item in last_radios
        )
        if sms_checked and not whatsapp_checked:
            logger.info("[Codex][Browser] 已选择并确认 SMS 短信通道")
            return
        logger.warning(
            "[Codex][Browser] SMS 选择后通道状态不稳定：round=%s/3 %s",
            selection_round, _phone_state_digest(last_state),
        )
        if selection_round < 3:
            _stop_sleep(0.2)
    _assert_sms_channel_or_raise(driver)


def _is_phone_code_state(state: dict) -> bool:
    url = str(state.get('url') or '').lower()
    if 'email-verification' in url:
        # 邮箱 OTP 页面也会出现 autocomplete=one-time-code，不能误判成手机验证码页。
        return False
    if 'phone-verification' in url:
        return True
    forms = state.get('forms') or []
    form_actions = ' '.join(str(f.get('action') or '') for f in forms).lower()
    if 'phone-verification' in form_actions:
        return True
    inputs = state.get('inputs') or []
    attrs = ' '.join(' '.join(str(i.get(k) or '') for k in ('type','name','id','autocomplete','placeholder')) for i in inputs).lower()
    body = str(state.get('bodyText') or '').lower()
    has_code_input = 'one-time-code' in attrs or 'otp' in attrs or 'code' in attrs
    phone_hint = (
        'phone' in url or 'phone' in form_actions
        or 'check your phone' in body
        or 'verification code we just sent' in body
        or 'enter the verification code' in body and ('text message' in body or 'phone' in body)
        or 'resend text message' in body
        or 'sent to +' in body
    )
    return bool(phone_hint and has_code_input)


def _is_phone_code_page(driver) -> bool:
    return _is_phone_code_state(_phone_page_state(driver))


def _is_add_phone_page(driver) -> bool:
    state = _phone_page_state(driver)
    url = str(state.get('url') or '').lower()
    inputs = state.get('inputs') or []
    attrs = ' '.join(' '.join(str(i.get(k) or '') for k in ('type','name','id','autocomplete')) for i in inputs).lower()
    return 'add-phone' in url or 'type tel' in attrs or 'phone' in attrs or 'tel' in attrs


_PHONE_INPUT_SELECTORS = [
    "input[type='tel']",
    "input[name='phone']",
    "input[name='phone_number']",
    "input[autocomplete='tel']",
    "input[id*='phone']",
    "input[placeholder*='Phone']",
    "input[placeholder*='phone']",
]


def _has_strict_add_phone_form(driver) -> bool:
    try:
        return bool(driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const form = document.querySelector('form[action*="/add-phone" i]')
          || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
        if (!form) return false;
        return !![...form.querySelectorAll('input[type="tel"], input[name="__reservedForPhoneNumberInput_tel"], input[autocomplete="tel"], input[name="phone"], input[name="phone_number"]')].find(visible);
        """))
    except Exception:
        return False


def _auth_origin(driver) -> str:
    try:
        parsed = urlparse(str(driver.current_url or ""))
        if parsed.scheme and parsed.netloc and parsed.hostname and parsed.hostname.endswith("openai.com"):
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return "https://auth.openai.com"


def _ensure_add_phone_input(driver, *, reason: str = ""):
    """确保当前页面回到 add-phone，并返回手机号输入框。

    换号时如果还停留在 phone-verification/OTP 页，必须先回到手机号页，
    再把新号码重新写入页面并重新提交。
    """
    if _has_strict_add_phone_form(driver):
        return _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=2)

    current = str(getattr(driver, "current_url", "") or "")
    if "email-verification" in current.lower():
        logger.info("[Codex][Browser] 当前仍在 email-verification，先等待授权流程自动跳转，避免 invalid_auth_step")
        _wait_after_email_otp_submit(driver, timeout=45)
        if _has_strict_add_phone_form(driver):
            return _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=2)
        current = str(getattr(driver, "current_url", "") or "")

    target = _auth_origin(driver).rstrip("/") + "/add-phone"
    logger.info(
        "[Codex][Browser] 当前不在手机号输入页，准备重新打开 add-phone 后换号：reason=%s url=%s target=%s",
        reason or "retry", current, target,
    )
    try:
        driver.get(target)
        human_delay("navigate")
        return _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=10)
    except Exception as first_exc:
        # 某些流程不允许直接打开 /add-phone，尝试浏览器返回到上一页。
        logger.info("[Codex][Browser] 直接打开 add-phone 未拿到输入框，尝试 history back：%s", str(first_exc)[:160])
        try:
            driver.back()
            human_delay("navigate")
            return _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=8)
        except Exception as back_exc:
            raise RuntimeError(
                f"无法回到手机号输入页以重新换号: direct={type(first_exc).__name__}: {first_exc}; "
                f"back={type(back_exc).__name__}: {back_exc}; state={_phone_page_state(driver)}"
            )


_PHONE_COUNTRY_NAME_ALIASES = {
    "BN": ("Brunei Darussalam",),
    "BO": ("Bolivia, Plurinational State of",),
    "CD": ("Democratic Republic of the Congo", "Congo, The Democratic Republic of the"),
    "CG": ("Republic of the Congo",),
    "CI": ("Ivory Coast", "Cote d'Ivoire"),
    "CV": ("Cabo Verde",),
    "CZ": ("Czechia",),
    "GB": ("UK", "Great Britain"),
    "IR": ("Iran, Islamic Republic of",),
    "KP": ("Korea, Democratic People's Republic of",),
    "KR": ("Korea, Republic of", "Republic of Korea"),
    "LA": ("Lao People's Democratic Republic",),
    "MD": ("Moldova, Republic of",),
    "MK": ("North Macedonia",),
    "MM": ("Burma",),
    "PS": ("Palestine, State of",),
    "RU": ("Russian Federation",),
    "SZ": ("Eswatini",),
    "SY": ("Syrian Arab Republic",),
    "TL": ("East Timor",),
    "TR": ("Türkiye", "Turkiye"),
    "TW": ("Taiwan, Province of China",),
    "TZ": ("Tanzania, United Republic of",),
    "US": ("USA", "United States of America"),
    "VA": ("Vatican City", "Holy See"),
    "VE": ("Venezuela, Bolivarian Republic of",),
    "VN": ("Viet Nam",),
    "XK": ("Kosovo",),
}


def _phone_country_identity(phone: str) -> dict:
    """只根据 E.164 号码解析页面国家，不读取 IP、价格或供应商排序。"""
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if not digits:
        raise RuntimeError("phone_country_sync_failed: 手机号缺少数字")
    e164 = f"+{digits}"
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException as exc:
        raise RuntimeError(f"phone_country_sync_failed: E.164 号码解析失败 phone={sms_provider._mask_phone(e164)}") from exc
    if not phonenumbers.is_possible_number(parsed):
        raise RuntimeError(f"phone_country_sync_failed: E.164 号码长度无效 phone={sms_provider._mask_phone(e164)}")

    regions = tuple(
        region for region in phonenumbers.region_codes_for_country_code(parsed.country_code)
        if region and region != "001"
    )
    region = str(phonenumbers.region_code_for_number(parsed) or "").upper()
    if not region and len(regions) == 1:
        region = regions[0]
    if not region:
        raise RuntimeError(f"phone_country_sync_failed: E.164 号码无法确定国家 phone={sms_provider._mask_phone(e164)}")

    names = [
        phone_geocoder.country_name_for_number(parsed, "en"),
        *_PHONE_COUNTRY_NAME_ALIASES.get(region, ()),
        region,
    ]
    aliases = []
    seen = set()
    for value in names:
        value = str(value or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            aliases.append(value)
    return {
        "e164": e164,
        "dialCode": str(parsed.country_code),
        "region": region,
        "countryName": str(names[0] or aliases[0]),
        "aliases": aliases,
        "allowCodeOnly": len(regions) <= 1,
    }


def _select_phone_country(driver, phone: str, *, timeout: int = 8) -> dict:
    """同步 React-Aria 国家控件，并返回已选国家的拨号区号。"""
    identity = _phone_country_identity(phone)
    digits = identity["e164"].lstrip("+")
    expected_dial_code = identity["dialCode"]
    country_aliases = identity["aliases"]
    allow_code_only = identity["allowCodeOnly"]

    result = driver.execute_script(r"""
    const digits = String(arguments[0] || '').replace(/\D+/g, '');
    const expectedDialCode = String(arguments[1] || '').replace(/\D+/g, '');
    const aliases = Array.isArray(arguments[2]) ? arguments[2].map(String).filter(Boolean) : [];
    const allowCodeOnly = Boolean(arguments[3]);
    const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    const form = document.querySelector('form[action*="/add-phone" i]')
      || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
    if (!form) return {ok:false, error:'missing_add_phone_form'};
    const meta = el => [
      el?.textContent, el?.label, el?.value, el?.id, el?.getAttribute?.('name'),
      el?.getAttribute?.('aria-label'), el?.getAttribute?.('data-key'),
      el?.getAttribute?.('data-value'), el?.getAttribute?.('data-country-code'),
      el?.getAttribute?.('data-dial-code'), ...Object.values(el?.dataset || {}),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
    const normalize = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim();
    const findCode = el => {
      const match = meta(el).match(/\+(\d{1,4})\b/);
      return match ? match[1] : '';
    };
    const findAlias = el => {
      const text = normalize(meta(el));
      const tokens = new Set(text.split(/\s+/).filter(Boolean));
      return aliases.find(alias => {
        const target = normalize(alias);
        if (!target) return false;
        if (target.length <= 3 && !target.includes(' ')) return tokens.has(target);
        return text === target || text.startsWith(target + ' ') || text.endsWith(' ' + target)
          || text.includes(' ' + target + ' ');
      }) || '';
    };
    const aliasScore = el => {
      const text = normalize(meta(el));
      const tokens = new Set(text.split(/\s+/).filter(Boolean));
      let best = 0;
      for (const alias of aliases) {
        const target = normalize(alias);
        if (!target) continue;
        if (target.length <= 3 && !target.includes(' ')) {
          if (tokens.has(target)) best = Math.max(best, 3);
        } else if (text === target) {
          best = Math.max(best, 5);
        } else if (text.startsWith(target + ' ')) {
          best = Math.max(best, 4);
        } else if (text.endsWith(' ' + target)) {
          best = Math.max(best, 3);
        } else if (text.includes(' ' + target + ' ')) {
          best = Math.max(best, 1);
        }
      }
      return best;
    };
    // React-Aria 的 hidden select 持有完整国家集合，虚拟 listbox 通常只挂载
    // 当前可见项。参考项目优先按 E.164 前缀更新 select；这里再叠加国家别名，
    // 避免 +1 等共享区号选错国家。
    const selects = [...new Set([
      ...form.querySelectorAll('[data-testid="hidden-select-container"] select, .react-aria-Select select, select'),
      ...document.querySelectorAll('select[name*="country" i], select[id*="country" i], select[aria-label*="country" i], select[name*="dial" i], select[id*="dial" i]'),
    ])].filter(el => !el.disabled);
    const aliasMatches = [];
    const codeMatches = [];
    for (const select of selects) {
      for (const option of [...select.options]) {
        const code = findCode(option);
        const alias = findAlias(option);
        if (alias && (!code || code === expectedDialCode)) {
          aliasMatches.push({select, option, code:code || expectedDialCode, alias, score:aliasScore(option)});
        } else if (allowCodeOnly && code === expectedDialCode) {
          codeMatches.push({select, option, code, alias:''});
        }
      }
    }
    const matches = aliasMatches.length
      ? aliasMatches.sort((a, b) => (b.score || 0) - (a.score || 0) || b.code.length - a.code.length)
      : codeMatches.sort((a, b) => b.code.length - a.code.length);
    if (matches.length) {
      const {select, option, code, alias} = matches[0];
      const changed = String(select.value) !== String(option.value);
      return {
        ok:true, mode:'native_select', select, optionValue:String(option.value || ''),
        dialCode:code || expectedDialCode, selectedText:meta(option),
        selectedKey:String(option.value || option.getAttribute('data-key') || ''),
        countryName:alias || '', selectedChanged:changed,
      };
    }

    // 没有完整原生选项时才打开可见控件，兼容无 hidden select 的页面。
    const triggers = [...form.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"]')].filter(visible);
    const score = el => {
      const text = meta(el).toLowerCase();
      return (/country|dial|calling|phone.*code|国家|国番号|電話番号/.test(text) ? 20 : 0)
        + (el.getAttribute('aria-haspopup') === 'listbox' ? 5 : 0);
    };
    triggers.sort((a, b) => score(b) - score(a));
    const trigger = triggers[0];
    if (trigger) {
      trigger.scrollIntoView({block:'center'});
      return {ok:false, opened:true, mode:'listbox', trigger, triggerText:meta(trigger)};
    }

    return {ok:false, error:'missing_country_control'};
    """, digits, expected_dial_code, country_aliases, allow_code_only) or {}

    sleep_fn = globals().get("_stop_sleep") or time.sleep
    selected = dict(result) if result.get("ok") else None
    if selected and selected.get("mode") == "native_select":
        native_select = selected.get("select")
        option_value = str(selected.get("optionValue") or "")
        try:
            if native_select is not None and callable(getattr(native_select, "select_option", None)):
                native_select.select_option(value=option_value)
            elif native_select is not None:
                # 标准 Selenium 回退；Cloak 路径使用上面的 Playwright 原生选择。
                from selenium.webdriver.support.ui import Select
                Select(native_select).select_by_value(option_value)
        except Exception as select_exc:
            logger.warning("[Codex][Browser] 原生国家下拉选择失败，回退 DOM 事件：%s", str(select_exc)[:160])
            if native_select is not None:
                driver.execute_script(r"""
                const select = arguments[0];
                const value = String(arguments[1] || '');
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
                if (setter) setter.call(select, value); else select.value = value;
                [...select.options].forEach(option => { option.selected = option.value === value; });
                select.dispatchEvent(new Event('input', {bubbles:true}));
                select.dispatchEvent(new Event('change', {bubbles:true}));
                """, native_select, option_value)
        sleep_fn(0.35)
    if not selected:
        if not result.get("opened"):
            raise RuntimeError(f"phone_country_sync_failed: 找不到国家控件 result={result} state={_phone_page_state(driver)}")
        trigger = result.get("trigger")
        if trigger is not None:
            try:
                _human_click(driver, trigger, label="codex_phone_country")
            except Exception as click_exc:
                logger.warning("[Codex][Browser] 真实打开国家控件失败，回退元素点击：%s", str(click_exc)[:160])
                driver.execute_script("arguments[0].click();", trigger)
        end = time.time() + max(1, timeout)
        while time.time() < end:
            selected = driver.execute_script(r"""
            const digits = String(arguments[0] || '').replace(/\D+/g, '');
            const expectedDialCode = String(arguments[1] || '').replace(/\D+/g, '');
            const aliases = Array.isArray(arguments[2]) ? arguments[2].map(String).filter(Boolean) : [];
            const allowCodeOnly = Boolean(arguments[3]);
            const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
              && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
            const meta = el => [
              el?.textContent, el?.getAttribute?.('aria-label'), el?.getAttribute?.('data-key'),
              el?.getAttribute?.('data-value'), el?.getAttribute?.('data-country-code'),
              el?.getAttribute?.('data-dial-code'), ...Object.values(el?.dataset || {}),
            ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
            const normalize = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
              .toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim();
            const findAlias = text => {
              const normalized = normalize(text);
              const tokens = new Set(normalized.split(/\s+/).filter(Boolean));
              return aliases.find(alias => {
                const target = normalize(alias);
                if (!target) return false;
                if (target.length <= 3 && !target.includes(' ')) return tokens.has(target);
                return normalized === target || normalized.startsWith(target + ' ')
                  || normalized.endsWith(' ' + target) || normalized.includes(' ' + target + ' ');
              }) || '';
            };
            const items = [...document.querySelectorAll('[role="option"], [role="listbox"] li')]
              .filter(visible).map(option => {
                const text = meta(option);
                const allCodes = [...text.matchAll(/\+(\d{1,4})\b/g)].map(m => m[1]);
                const codes = allCodes.filter(code => code === expectedDialCode);
                return {
                  option, text, code:codes[0] || '', alias:findAlias(text),
                  codeCompatible:!allCodes.length || allCodes.includes(expectedDialCode),
                };
              });
            const aliasMatches = items.filter(item => item.alias && item.codeCompatible);
            const codeMatches = allowCodeOnly
              ? items.filter(item => item.code).sort((a, b) => b.code.length - a.code.length)
              : [];
            const matches = aliasMatches.length ? aliasMatches : codeMatches;
            if (!matches.length) return null;
            const target = matches[0];
            target.option.scrollIntoView({block:'nearest'});
            return {
              mode:'listbox', option:target.option, dialCode:target.code || expectedDialCode, selectedText:target.text,
              selectedKey:String(target.option.getAttribute('data-key') || target.option.getAttribute('data-value') || target.option.id || ''),
              countryName:target.alias || target.text.replace(/\s*\(\s*\+\d{1,4}[^)]*\).*$/, '').trim(),
              selectedChanged:true,
            };
            """, digits, expected_dial_code, country_aliases, allow_code_only)
            if selected:
                option = selected.pop("option", None) if isinstance(selected, dict) else None
                if option is not None:
                    try:
                        _human_click(driver, option, label="codex_phone_country_option")
                    except Exception as click_exc:
                        logger.warning("[Codex][Browser] 真实选择国家选项失败，回退元素点击：%s", str(click_exc)[:160])
                        driver.execute_script("arguments[0].click();", option)
                    sleep_fn(0.35)
                break
            _stop_sleep(0.2)
        if not selected:
            raise RuntimeError(
                "phone_country_sync_failed: 国家列表中找不到号码对应国家 "
                f"target={country_aliases} dial={expected_dial_code} state={_phone_page_state(driver)}"
            )

    sleep_fn = globals().get("_stop_sleep") or time.sleep
    sleep_fn(0.35)
    confirmed = driver.execute_script(r"""
    const expectedCode = String(arguments[0] || '').replace(/\D+/g, '');
    const aliases = Array.isArray(arguments[1]) ? arguments[1].map(String).filter(Boolean) : [];
    const allowCodeOnly = Boolean(arguments[2]);
    const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    const form = document.querySelector('form[action*="/add-phone" i]')
      || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
    if (!form) return {ok:false, error:'missing_add_phone_form'};
    const countrySelects = [...new Set([
      ...form.querySelectorAll('select'),
      ...document.querySelectorAll('select[name*="country" i], select[id*="country" i], select[aria-label*="country" i], select[name*="dial" i], select[id*="dial" i]'),
    ])];
    const selectedOptions = countrySelects
      .map(select => select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null).filter(Boolean);
    const controls = [
      ...selectedOptions,
      ...[...form.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"]')].filter(visible),
    ];
    const combined = controls.map(el => [
      el.textContent, el.label, el.value, el.id, el.getAttribute?.('aria-label'),
      el.getAttribute?.('data-key'), el.getAttribute?.('data-value'), ...Object.values(el.dataset || {}),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim()).join(' | ');
    const normalize = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim();
    const normalized = normalize(combined);
    const tokens = new Set(normalized.split(/\s+/).filter(Boolean));
    const matchedAlias = aliases.find(alias => {
      const target = normalize(alias);
      if (!target) return false;
      if (target.length <= 3 && !target.includes(' ')) return tokens.has(target);
      return normalized === target || normalized.startsWith(target + ' ')
        || normalized.endsWith(' ' + target) || normalized.includes(' ' + target + ' ');
    }) || '';
    const codes = [...combined.matchAll(/\+(\d{1,4})\b/g)].map(m => m[1]);
    const codesCompatible = !codes.length
      || (codes.includes(expectedCode) && codes.every(code => code === expectedCode));
    return {
      ok:(!!matchedAlias && codesCompatible)
        || (allowCodeOnly && codesCompatible && codes.includes(expectedCode)),
      combined, codes, matchedAlias, codesCompatible,
    };
    """, expected_dial_code, country_aliases, allow_code_only) or {}
    if not confirmed.get("ok"):
        raise RuntimeError(
            f"phone_country_sync_failed: 国家控件点击后未保持选中 selected={selected} confirmed={confirmed} "
            f"state={_phone_page_state(driver)}"
        )
    return {
        "ok": True,
        "mode": str(selected.get("mode") or "listbox"),
        "dialCode": str(selected.get("dialCode") or ""),
        "selectedText": str(selected.get("selectedText") or confirmed.get("combined") or ""),
        "selectedKey": str(selected.get("selectedKey") or ""),
        "countryName": str(selected.get("countryName") or identity["countryName"]),
        "region": identity["region"],
        "countryAliases": list(country_aliases),
        "selectedChanged": bool(selected.get("selectedChanged", True)),
    }


def _set_phone_value(driver, phone: str, *, timeout: int = 10) -> dict:
    """按 FlowPilot 第 9 步逻辑填写 add-phone 表单。

    要点：
    - 所有元素 scoped 到 form[action*="/add-phone"]；
    - 可见 tel 输入框写入“页面期望显示的号码”；
    - 如果页面存在隐藏 input[name="phoneNumber"]，同步写入完整 E.164 号码；
    - 触发 input/change 并 blur，让 React/React-Aria 完成校验。
    """
    if not _has_strict_add_phone_form(driver):
        raise RuntimeError(f"当前不是 add-phone 手机号输入页，不能填写手机号: state={_phone_page_state(driver)}")
    country = _select_phone_country(driver, phone, timeout=min(timeout, 8))
    expected_dial_code = ''.join(ch for ch in str(country.get("dialCode") or "") if ch.isdigit())
    expected_digits = ''.join(ch for ch in str(phone or "") if ch.isdigit())
    if not expected_dial_code or not expected_digits.startswith(expected_dial_code):
        raise RuntimeError(
            f"phone_country_mismatch: 号码前缀与国家控件不一致 phone={sms_provider._mask_phone(phone)} country={country}"
        )
    result = driver.execute_script(r"""
    const rawPhone = String(arguments[0] || '').trim();
    const expectedDialCode = String(arguments[1] || '').replace(/\D+/g, '');
    const e164 = rawPhone.startsWith('+') ? rawPhone : ('+' + rawPhone.replace(/\D+/g, ''));
    const digits = e164.replace(/\D+/g, '');
    const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
    const form = document.querySelector('form[action*="/add-phone" i]')
      || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
    if (!form) {
      return {ok:false, error:'missing_add_phone_form', url: location.href};
    }
    const phoneInput = [...form.querySelectorAll('input[type="tel"], input[name="__reservedForPhoneNumberInput_tel"], input[autocomplete="tel"], input[name="phone"], input[name="phone_number"]')]
      .find(visible);
    if (!phoneInput) {
      return {ok:false, error:'missing_phone_input', url: location.href};
    }

    const hiddenPhoneNumberInput = form.querySelector('input[name="phoneNumber"]');
    if (!expectedDialCode || !digits.startsWith(expectedDialCode)) {
      return {ok:false, error:'phone_country_mismatch', e164, expectedDialCode, url:location.href};
    }

    // 国家控件已经同步；可见框填 national number，隐藏字段填完整 E.164。
    const visibleValue = digits.slice(expectedDialCode.length);
    if (!visibleValue) {
      return {ok:false, error:'missing_national_number', e164, expectedDialCode, url:location.href};
    }

    const setNativeValue = (el, value, focus = false) => {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (focus) el.focus();
      if (setter) setter.call(el, ''); else el.value = '';
      try { el.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, inputType:'deleteContentBackward', data:null})); } catch (_) {}
      el.dispatchEvent(new Event('input', {bubbles:true}));
      if (setter) setter.call(el, value); else el.value = value;
      try { el.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, inputType:'insertText', data:value})); } catch (_) {}
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
    };

    phoneInput.scrollIntoView({block:'center'});
    setNativeValue(phoneInput, visibleValue, true);
    if (hiddenPhoneNumberInput) setNativeValue(hiddenPhoneNumberInput, e164);
    phoneInput.blur();
    document.body?.focus?.();
    return {
      ok: true,
      e164,
      visibleValue,
      actualVisible: phoneInput.value || '',
      hiddenValue: hiddenPhoneNumberInput ? (hiddenPhoneNumberInput.value || '') : '',
      hasHidden:!!hiddenPhoneNumberInput,
      dialCode:expectedDialCode,
      inputName: phoneInput.getAttribute('name') || '',
      inputId: phoneInput.id || '',
      url: location.href,
    };
    """, phone, expected_dial_code)
    if not result or not result.get("ok"):
        reason = "phone_country_mismatch" if (result or {}).get("error") == "phone_country_mismatch" else "phone_value_write_failed"
        raise RuntimeError(f"{reason}: 手机号写入失败 result={result} state={_phone_page_state(driver)}")

    # Native setters establish the exact DOM values. Cloak then uses its saved
    # native Page.fill so React receives the controlled input update atomically;
    # a final native setter below restores the exact E.164 after formatting.
    phone_input = _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=2)
    atomic_fill = getattr(phone_input, "fill", None)
    if callable(atomic_fill):
        visible_value = str(result.get("visibleValue") or "")
        try:
            atomic_fill(visible_value, timeout=max(1000, int(timeout) * 1000))
        except Exception as exc:
            raise RuntimeError(
                f"phone_react_state_sync_failed: 原子填值失败: {type(exc).__name__}: {exc}"
            ) from exc
        _stop_sleep(0.2)
        input_state = driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const form = document.querySelector('form[action*="/add-phone" i]')
          || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
        const phoneInput = [...(form?.querySelectorAll('input[type="tel"], input[name="__reservedForPhoneNumberInput_tel"], input[autocomplete="tel"], input[name="phone"], input[name="phone_number"]') || [])].find(visible);
        const hidden = form?.querySelector('input[name="phoneNumber"]');
        if (!phoneInput) return {actualVisible:'', hiddenValue:hidden?.value || '', hasHidden:!!hidden};
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        const emit = (el, value) => {
          if (!el) return;
          if (setter) setter.call(el, String(value || '')); else el.value = String(value || '');
          el.dispatchEvent(new Event('input', {bubbles:true}));
          el.dispatchEvent(new Event('change', {bubbles:true}));
        };
        emit(phoneInput, String(arguments[0] || ''));
        emit(hidden, String(arguments[1] || ''));
        phoneInput.blur();
        return {actualVisible: phoneInput.value || '', hiddenValue: hidden?.value || '', hasHidden:!!hidden};
        """, visible_value, str(result.get("e164") or phone)) or {}
        result.update({
            "actualVisible": str(input_state.get("actualVisible") or ""),
            "hiddenValue": str(input_state.get("hiddenValue") or ""),
            "hasHidden": bool(input_state.get("hasHidden")),
            "inputMethod": "native_page_fill",
        })
    else:
        result["inputMethod"] = "native_setter"

    result.update({
        "countryMode": country.get("mode"),
        "selectedText": country.get("selectedText"),
        "selectedKey": country.get("selectedKey"),
        "countryName": country.get("countryName"),
        "selectedChanged": country.get("selectedChanged", False),
    })
    actual = str(result.get("actualVisible") or "").strip()
    visible_value = str(result.get("visibleValue") or "").strip()
    hidden_value = str(result.get("hiddenValue") or "").strip()
    e164 = str(result.get("e164") or "").strip()
    # OpenAI/React-Aria 电话框会自动格式化，例如 +84925154291 -> +84 925 154 291。
    # 不能按界面字符串精确比较，只比较数字归一化后的值。
    actual_digits = ''.join(ch for ch in actual if ch.isdigit())
    visible_digits = ''.join(ch for ch in visible_value if ch.isdigit())
    e164_digits = ''.join(ch for ch in e164 if ch.isdigit())
    hidden_digits = ''.join(ch for ch in hidden_value if ch.isdigit())
    dial_digits = ''.join(ch for ch in str(result.get("dialCode") or expected_dial_code) if ch.isdigit())
    expected_visible_ok = bool(actual_digits) and (
        actual_digits == visible_digits
        or actual_digits == e164_digits
        or (dial_digits + actual_digits == e164_digits)
    )
    if not dial_digits or not e164_digits.startswith(dial_digits):
        raise RuntimeError(f"phone_country_mismatch: 号码前缀与国家控件不一致 result={result} state={_phone_page_state(driver)}")
    if not expected_visible_ok:
        raise RuntimeError(f"phone_value_mismatch: 手机号可见输入框校验失败 expected_digits={visible_digits or e164_digits} actual={actual} result={result} state={_phone_page_state(driver)}")
    if result.get("hasHidden") and hidden_digits != e164_digits:
        raise RuntimeError(f"phone_react_state_sync_failed: 隐藏字段校验失败 expected={e164} actual={hidden_value} result={result} state={_phone_page_state(driver)}")
    return result


def _blur_active_input_and_wait(driver, *, label: str = "输入完成") -> None:
    """输入手机号后移开焦点，并给前端校验/格式化留处理时间。"""
    try:
        driver.execute_script(r"""
        const active = document.activeElement;
        if (active && typeof active.blur === 'function') active.blur();
        document.body?.focus?.();
        document.dispatchEvent(new Event('change', {bubbles:true}));
        """)
    except Exception:
        pass
    seconds = random.uniform(1.8, 3.2)
    logger.info("[Codex][Browser] %s，已移开焦点，等待页面处理 %.1f 秒", label, seconds)
    _stop_sleep(seconds)


def _verify_add_phone_value_before_submit(
    driver, expected_e164: str, expected_dial_code: str = ""
) -> dict:
    identity = _phone_country_identity(expected_e164)
    resolved_dial_code = identity["dialCode"]
    requested_dial_code = ''.join(ch for ch in str(expected_dial_code or "") if ch.isdigit())
    if requested_dial_code and requested_dial_code != resolved_dial_code:
        raise RuntimeError(
            "phone_country_mismatch: 调用方区号与 E.164 号码不一致 "
            f"phone={sms_provider._mask_phone(expected_e164)} expected={requested_dial_code} resolved={resolved_dial_code}"
        )
    result = driver.execute_script(r"""
    const expected = String(arguments[0] || '').trim();
    const expectedDialCode = String(arguments[1] || '').replace(/\D+/g, '');
    const aliases = Array.isArray(arguments[2]) ? arguments[2].map(String).filter(Boolean) : [];
    const allowCodeOnly = Boolean(arguments[3]);
    const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    const form = document.querySelector('form[action*="/add-phone" i]')
      || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
    if (!form) return {ok:false, error:'missing_add_phone_form', url: location.href};
    const input = [...form.querySelectorAll('input[type="tel"], input[name="__reservedForPhoneNumberInput_tel"], input[autocomplete="tel"], input[name="phone"], input[name="phone_number"]')].find(visible);
    const hidden = form.querySelector('input[name="phoneNumber"]');
    const visibleValue = String(input?.value || '').trim();
    const hiddenValue = String(hidden?.value || '').trim();
    const digits = value => String(value || '').replace(/\D+/g, '');
    const expectedDigits = digits(expected);
    const visibleDigits = digits(visibleValue);
    const hiddenDigits = digits(hiddenValue);
    const selectedOptions = [...new Set([
      ...form.querySelectorAll('select'),
      ...document.querySelectorAll('select[name*="country" i], select[id*="country" i], select[aria-label*="country" i], select[name*="dial" i], select[id*="dial" i]'),
    ])].map(select => select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null).filter(Boolean);
    const controls = [
      ...selectedOptions,
      ...[...form.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"]')].filter(visible),
    ];
    const countryText = controls.map(el => [
      el.textContent, el.label, el.value, el.id, el.getAttribute?.('name'),
      el.getAttribute?.('aria-label'), el.getAttribute?.('data-key'),
      el.getAttribute?.('data-value'), el.getAttribute?.('data-country-code'),
      el.getAttribute?.('data-dial-code'), ...Object.values(el.dataset || {}),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim()).join(' | ');
    const normalize = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim();
    const normalizedCountry = normalize(countryText);
    const countryTokens = new Set(normalizedCountry.split(/\s+/).filter(Boolean));
    const matchedAlias = aliases.find(alias => {
      const target = normalize(alias);
      if (!target) return false;
      if (target.length <= 3 && !target.includes(' ')) return countryTokens.has(target);
      return normalizedCountry === target || normalizedCountry.startsWith(target + ' ')
        || normalizedCountry.endsWith(' ' + target)
        || normalizedCountry.includes(' ' + target + ' ');
    }) || '';
    const pageCodes = [...countryText.matchAll(/\+(\d{1,4})\b/g)].map(match => match[1])
      .filter(code => expectedDigits.startsWith(code)).sort((a, b) => b.length - a.length);
    const pageDialCode = pageCodes[0] || '';
    const countryOk = !!matchedAlias || (allowCodeOnly && pageDialCode === expectedDialCode);
    const visibleOk = !!visibleDigits && (
      visibleDigits === expectedDigits || (expectedDialCode + visibleDigits === expectedDigits)
    );
    const hiddenOk = !hidden || hiddenDigits === expectedDigits;
    const ok = countryOk && visibleOk && hiddenOk;
    return {
      ok, countryOk, visibleOk, hiddenOk, visibleValue, hiddenValue, expected,
      visibleDigits, hiddenDigits, expectedDigits, expectedDialCode,
      dialCode:expectedDialCode, pageDialCode, pageCodes, matchedAlias, countryText, url:location.href,
    };
    """, identity["e164"], resolved_dial_code, identity["aliases"], identity["allowCodeOnly"])
    if not result or not result.get("countryOk"):
        raise RuntimeError(
            f"phone_country_mismatch: 提交前国家区号校验失败 result={result} state={_phone_page_state(driver)}"
        )
    if not result.get("ok"):
        raise RuntimeError(
            f"phone_value_mismatch: 手机号提交前校验失败 result={result} state={_phone_page_state(driver)}"
        )
    return result


def _wait_page_settle_after_submit() -> None:
    """点击提交后先等待页面处理，再检查发送状态。"""
    seconds = random.uniform(2.0, 4.0)
    logger.info("[Codex][Browser] 已点击提交，等待页面发送/跳转处理 %.1f 秒后检查状态", seconds)
    _stop_sleep(seconds)


def _refresh_add_phone_for_retry(driver, *, reason: str = "") -> None:
    """发送失败/换号前刷新手机号页，避免旧错误状态和旧号码残留。"""
    try:
        logger.info("[Codex][Browser] 发送失败/准备换号，刷新手机号页面：%s", reason or "retry")
        driver.refresh()
        human_delay("navigate")
        try:
            _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=8)
            return
        except Exception:
            pass
        # 如果刷新后仍不在输入页，强制回 add-phone。
        target = _auth_origin(driver).rstrip("/") + "/add-phone"
        logger.info("[Codex][Browser] 刷新后未找到手机号输入框，重新打开：%s", target)
        driver.get(target)
        human_delay("navigate")
        _find_any(driver, _PHONE_INPUT_SELECTORS, timeout=8)
    except Exception as exc:
        logger.info("[Codex][Browser] 刷新手机号页失败，下一轮会再次尝试回到 add-phone：%s", str(exc)[:180])


def _click_add_phone_continue_button(driver, *, timeout: int = 10) -> dict:
    """点击 add-phone 表单里的 Continue/続行；单次 activation 不进行二次提交。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            btn = driver.execute_script(r"""
            const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const enabled = el => {
              if (!el) return false;
              if (el.disabled) return false;
              if (String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return false;
              return true;
            };
            const form = document.querySelector('form[action*="/add-phone" i]')
              || [...document.querySelectorAll('form')].find(f => /add-phone/i.test(f.getAttribute('action') || ''));
            if (!form) return null;
            const buttons = [...form.querySelectorAll('button[type="submit"], input[type="submit"]')];
            return buttons.find(b => visible(b) && enabled(b) && (b.getAttribute('data-dd-action-name') || '').toLowerCase() === 'continue')
              || buttons.find(b => visible(b) && enabled(b))
              || buttons.find(b => visible(b))
              || null;
            """)
            if btn:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                _stop_sleep(random.uniform(0.3, 0.8))
                try:
                    text = str(getattr(btn, 'text', '') or btn.get_attribute('value') or btn.get_attribute('data-dd-action-name') or '').strip()
                except Exception:
                    text = ''
                try:
                    # Each purchased activation gets exactly one submit action. A click error
                    # may occur after the browser dispatched the event and began navigation,
                    # so never issue requestSubmit on the same activation as a fallback.
                    _human_click(driver, btn, label="codex_phone_continue")
                    _wait_page_settle_after_submit()
                    return {"ok": True, "method": "human_click", "text": text}
                except Exception as click_exc:
                    logger.warning(
                        "[Codex][Browser] 手机号提交点击返回异常；不对当前号码二次提交，转入状态检查：%s: %s",
                        type(click_exc).__name__, str(click_exc)[:180],
                    )
                    _wait_page_settle_after_submit()
                    return {
                        "ok": False,
                        "method": "human_click_exception",
                        "text": text,
                        "click_error": f"{type(click_exc).__name__}: {str(click_exc)[:160]}",
                    }
        except Exception as exc:
            last = exc
        _stop_sleep(0.25)
    raise RuntimeError(f"submit_missing: add-phone Continue/続行 submit button not found last={last} state={_phone_page_state(driver)}")


def _start_add_phone_response_watch(driver):
    """在 Cloak 页面上暂存 add-phone/send 响应，供失败诊断读取。"""
    page = getattr(driver, "page", None)
    if page is None or not callable(getattr(page, "on", None)):
        return None
    responses = []

    def _on_response(response):
        try:
            url = str(getattr(response, "url", "") or "")
        except Exception:
            return
        if "/api/accounts/add-phone/send" in url:
            responses.append(response)

    try:
        page.on("response", _on_response)
        return {"page": page, "callback": _on_response, "responses": responses}
    except Exception:
        return None


def _finish_add_phone_response_watch(watch) -> str | None:
    if not watch:
        return None
    page = watch.get("page")
    callback = watch.get("callback")
    try:
        remover = getattr(page, "remove_listener", None) or getattr(page, "off", None)
        if callable(remover):
            remover("response", callback)
    except Exception:
        pass
    last_diagnostic = None
    for response in list(watch.get("responses") or []):
        try:
            status = int(getattr(response, "status", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if 200 <= status < 400:
            continue
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                selected = {
                    key: str(payload.get(key))[:240]
                    for key in ("error", "message", "detail", "code", "type", "requestId", "request_id")
                    if payload.get(key) is not None
                }
                detail = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))[:600]
        except Exception:
            try:
                detail = " ".join(str(response.text() or "").split())[:600]
            except Exception:
                detail = "response_body_unavailable"
        diagnostic = f"status={status} detail={detail or '-'}"
        logger.warning("[Codex][Browser] add-phone/send 响应诊断：%s", diagnostic)
        last_diagnostic = diagnostic
    return last_diagnostic


def _phone_state_digest(state: dict) -> str:
    """把 add-phone 页的关键状态压成一行，便于在日志里完整看出卡点。

    失败信息里的 state 会被截断到 240 字符，只能看到第一个 radio，
    无法判断 SMS 到底有没有保住。这里单独输出通道勾选与号码字段状态。
    """
    if not isinstance(state, dict):
        return "state=<invalid>"
    channels = " ".join(
        f"{str(item.get('value') or '-')}={'Y' if item.get('checked') else 'n'}"
        for item in (state.get("radios") or [])
    ) or "-"
    fields = []
    for item in (state.get("inputs") or []):
        itype = str(item.get("type") or "").lower()
        name = str(item.get("name") or "").lower()
        if itype == "tel" or "phone" in name or str(item.get("autocomplete") or "").lower() == "tel":
            fields.append(
                f"{itype or 'tel'}({name or '-'}):len={len(str(item.get('value') or ''))}"
                f":invalid={item.get('ariaInvalid') or '-'}"
            )
    return f"channels=[{channels}] fields=[{'; '.join(fields) or '-'}]"


# 通道/号码/提交的任何失败都会先释放当前激活，再进入下一次取号。
_PHONE_SWITCH_HINTS = (
    "invalid_phone",
    "invalid_phone_code",
    "delivery_refused",
    "send_limited",
    "phone_in_use",
    "whatsapp_channel_reverted",
    "sms_channel_select_failed",
    "whatsapp_channel:",
    "invalid_auth_step",
)


def _prepare_and_submit_add_phone(driver, e164: str, *, label: str = "") -> dict:
    """在 add-phone 页填写号码、选择 SMS 通道并只提交一次。"""
    logger.info("[Codex][Browser] 准备手机号输入页，重新设置新手机号%s", label)
    _ensure_add_phone_input(driver, reason=f"add-phone{label}")
    phone_fill = _set_phone_value(driver, e164, timeout=10)
    logger.info(
        "[Codex][Browser] 已重新设置手机号：e164=%s visible=%s hidden=%s inputMethod=%s dialCode=%s country=%s",
        phone_fill.get("e164"), phone_fill.get("actualVisible"), phone_fill.get("hiddenValue") or "-",
        phone_fill.get("inputMethod") or "native_setter", phone_fill.get("dialCode") or "-",
        (str(phone_fill.get("selectedText") or "-") + (" [changed]" if phone_fill.get("selectedChanged") else "")),
    )
    _blur_active_input_and_wait(driver, label="手机号输入完成")
    dial_code = str(phone_fill.get("dialCode") or "")
    phone_verify = _verify_add_phone_value_before_submit(driver, e164, dial_code)
    logger.info(
        "[Codex][Browser] 手机号提交前校验通过：visible=%s hidden=%s dialCode=%s country=%s",
        phone_verify.get("visibleValue"), phone_verify.get("hiddenValue") or "-",
        phone_verify.get("dialCode") or "-", phone_verify.get("countryText") or "-",
    )
    logger.info("[Codex][Browser] 检查并选择 SMS 短信通道")
    # 只点击一次 SMS。重复点击 radio 会让 React 重建 add-phone 表单，
    # 造成手机号状态回退为空并触发 Phone number required。
    _select_sms_channel_or_raise(driver)
    # 给通道选择的异步状态更新留时间，但不再次点击 radio。
    _blur_active_input_and_wait(driver, label="短信通道确认完成")
    # 通道选择/重渲染后只重新填写丢失的手机号，保持 SMS 选择状态不变。
    try:
        _verify_add_phone_value_before_submit(driver, e164, dial_code)
    except RuntimeError as verify_exc:
        logger.warning(
            "[Codex][Browser] 选择短信通道后手机号已丢失，重新填写：%s",
            str(verify_exc)[:200],
        )
        phone_fill = _set_phone_value(driver, e164, timeout=10)
        _blur_active_input_and_wait(driver, label="手机号重填完成")
        phone_verify = _verify_add_phone_value_before_submit(
            driver, str(phone_fill.get("e164") or e164), str(phone_fill.get("dialCode") or dial_code),
        )
        logger.info(
            "[Codex][Browser] 重填后校验通过：visible=%s hidden=%s dialCode=%s country=%s",
            phone_verify.get("visibleValue"), phone_verify.get("hiddenValue") or "-",
            phone_verify.get("dialCode") or "-", phone_verify.get("countryText") or "-",
        )
    _assert_sms_channel_or_raise(driver)
    submit_info = _click_add_phone_continue_button(driver, timeout=10)
    logger.info("[Codex][Browser] 手机号提交动作已派发：%s，开始检查验证码页/错误状态", submit_info)
    return submit_info


def _wait_after_phone_send(driver, timeout: int = 12) -> str:
    """Observe one add-phone submission; never resubmit the same paid activation."""
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        _stop_sleep(1)
        last = _phone_page_state(driver)
        # Check the code page first: its copy can contain send/limit/check markers.
        if _is_phone_code_state(last):
            return 'code_page'
        body = str(last.get('bodyText') or '')
        reason = _classify_phone_page_failure(last)
        if reason:
            logger.warning(
                "[Codex][Browser] 手机号页判定失败：reason=%s url=%s %s body=%s",
                reason, str(last.get("url") or "-")[:120], _phone_state_digest(last),
                " ".join(body.split())[:260],
            )
            raise RuntimeError(f"{reason}: {body[:240]}")
        if _is_add_phone_page(driver):
            invalid = any(str(i.get('ariaInvalid') or '').lower() == 'true' for i in (last.get('inputs') or []))
            if invalid:
                raise RuntimeError(f"invalid_phone: add-phone input aria-invalid state={last}")
    if _is_phone_code_state(last) or _is_phone_code_page(driver):
        return 'code_page'
    if _is_add_phone_page(driver):
        logger.warning(
            "[Codex][Browser] 单次手机号提交后仍停留在 add-phone，不会对当前 activation 重复提交：url=%s %s",
            str(last.get("url") or "-")[:120], _phone_state_digest(last),
        )
        raise RuntimeError(f"send_not_accepted: 单次提交后仍停留在 add-phone state={last}")
    return 'unknown'


def _wait_after_phone_otp_submit(driver, timeout: int = 20) -> str:
    """手机验证码提交后等待结果。

    成功时通常会跳出 phone-verification，进入 consent/workspace/callback；不能在提交后
    3 秒立刻读取旧页面文案并按 send_limited 判失败。只有明确仍在手机号流程且出现错误时
    才返回失败。
    """
    end = time.time() + timeout
    last = {}
    while time.time() < end:
        _stop_sleep(1)
        callback = _extract_callback_url_from_any_window(driver)
        if callback:
            return "callback"
        last = _phone_page_state(driver)
        # 已离开手机验证码/加手机号页面，说明验证码被接受，后续交给 consent/callback 流程。
        if not _is_phone_code_state(last) and not _is_add_phone_page(driver):
            return "left_phone_flow"
        # 仍在验证码页时，只把明确错误当失败；普通 Check your phone 页面继续等。
        if _is_phone_code_state(last):
            inputs = last.get('inputs') or []
            invalid = any(str(i.get('ariaInvalid') or '').lower() == 'true' for i in inputs)
            body = str(last.get('bodyText') or '').lower()
            if invalid or any(k in body for k in (
                'invalid code', 'incorrect code', 'wrong code', 'expired code',
                'code is invalid', 'code was invalid', '验证码无效', '验证码错误', '验证码已过期',
                '認証コードが無効', 'コードが正しく',
            )):
                raise RuntimeError(f"invalid_phone_code: {(last.get('bodyText') or '')[:240]}")
            continue
        reason = _classify_phone_page_failure(last)
        if reason:
            raise RuntimeError(f"{reason}: {(last.get('bodyText') or '')[:240]}")
    # 超时后再看一次：如果已经离开手机号流程，视为通过；如果仍在验证码页但没明确错误，交给后续流程继续试。
    current = str(getattr(driver, "current_url", "") or "")
    if _is_callback_url(current):
        return "callback"
    last = _phone_page_state(driver)
    if not _is_phone_code_state(last) and not _is_add_phone_page(driver):
        return "left_phone_flow"
    if _is_phone_code_state(last):
        return "still_code_page"
    return "unknown"


def _phone_otp_outcome_accepted(outcome: str) -> bool:
    """只有明确离开手机号流程或捕获 callback 才确认 OTP 成功。"""
    return str(outcome or "") in ("callback", "left_phone_flow")


def _classify_phone_page_failure(state: dict) -> str:
    if _is_phone_code_state(state):
        return ''
    # 页面会同时展示 SMS/WhatsApp 文案。这里必须区分两种完全不同的情况：
    #   - 页面只提供 WhatsApp（没有 SMS 选项）：可能与该号码所属地区有关，换号有意义；
    #   - 我们刚成功勾选 SMS，提交后 WhatsApp 又被勾上：是选择没保住的本机交互问题，
    #     与号码无关，换号解决不了，只会继续烧号。
    # 两者共用一个标记会让重试策略无从选择，因此拆开上报。
    radios = state.get('radios') or []
    radio_values = [str(r.get('value', '')).lower().replace(' ', '') for r in radios]
    has_sms_option = any('sms' in value or 'text' in value for value in radio_values)
    if any('whatsapp' in value and radio.get('checked') for value, radio in zip(radio_values, radios)):
        # SMS 选项存在却让 WhatsApp 保持勾选 → 通道选择回退，不是号码问题。
        return 'whatsapp_channel_reverted' if has_sms_option else 'whatsapp_channel'
    if any('whatsapp' in value for value in radio_values) and not has_sms_option:
        return 'whatsapp_channel'
    text = str(state.get('bodyText') or '').lower()
    if 'invalid_auth_step' in text or 'invalid auth step' in text:
        return 'invalid_auth_step'
    if any(k in text for k in (
        'phone number required', 'phone number is required', 'please enter a phone number',
        '请输入手机号', '请输入手机号码', '手机号必填', '電話番号を入力',
    )):
        # 这段文案是 add-phone 页的固定说明（"Phone number required / Add your
        # phone number to continue..."），输入框里明明有号码时它照样在。只有
        # 号码确实为空才算真缺号码；否则会把“表单根本没提交成功”误报成号码问题，
        # 掩盖真实原因并白换一个号码。
        phone_filled = any(
            str(item.get('value') or '').strip()
            for item in (state.get('inputs') or [])
            if str(item.get('type') or '').lower() == 'tel'
            or 'phone' in str(item.get('name') or '').lower()
            or str(item.get('autocomplete') or '').lower() == 'tel'
        )
        if not phone_filled:
            return 'phone_number_required'
        return ''
    if any(k in text for k in ('invalid phone', 'not a valid phone', 'phone number is not valid', '号码无效', '手机号无效')):
        return 'invalid_phone'
    if any(k in text for k in (
        'cannot send', 'could not send', 'unable to send', 'failed to send', 'send failed',
        '发送失败', '发送失败了', '无法发送', '不能发送', '无法向',
        '送信できません', '送信に失敗', '送信できなかった',
    )):
        return 'delivery_refused'
    if any(k in text for k in ('too many', 'rate limit', 'throttle', '频繁', '限流')):
        return 'send_limited'
    return ''

def _sleep_before_phone_retry(
    attempt: int,
    max_retries: int,
    *,
    prefix: str = "[Codex][Browser]",
    reason: str = "",
) -> None:
    """按服务端拒绝类型退避，避免在 fraud_guard 窗口内连续换号。"""
    if attempt >= max_retries:
        return
    reason_text = str(reason or "").lower()
    fraud_guard = any(marker in reason_text for marker in (
        "fraud_guard",
        "suspicious behavior from phone",
    ))
    if fraud_guard:
        sms_cfg = getattr(sms_provider, "_cfg", None)
        minimum = max(1, int(getattr(sms_cfg, "SMS_FRAUD_GUARD_RETRY_MIN", 20) or 20))
        maximum = max(minimum, int(getattr(sms_cfg, "SMS_FRAUD_GUARD_RETRY_MAX", 45) or 45))
        seconds = random.uniform(minimum, maximum)
        logger.info(
            "%s 检测到 fraud_guard，换号前退避 %.1f 秒（范围 %s-%ss）",
            prefix, seconds, minimum, maximum,
        )
    else:
        seconds = random.uniform(3.0, 8.0)
        logger.info("%s 换号前随机等待 %.1f 秒", prefix, seconds)
    _stop_sleep(seconds)


def _do_phone_verification_if_present(driver) -> dict | None:
    """如果页面要求手机号验证，则用当前 sms_provider 自动完成。"""
    provider = sms_provider._provider()
    http = sms_provider._http()
    max_retries = max(1, int(getattr(sms_provider._cfg, "SMS_MAX_RETRIES", 10) or 10)) if hasattr(sms_provider, "_cfg") else 10
    # 预检阶段可能直接抛出短信依赖错误；异常清理路径也会读取该变量。
    activation_id = None
    try:
        # 如果页面没有手机号输入框，直接返回。
        try:
            end_detect = time.time() + 8
            while time.time() < end_detect and not _has_strict_add_phone_form(driver):
                # 如果已经在验证码页，说明手机步骤之前已提交过；继续处理验证码页，不应当跳过。
                if _is_phone_code_page(driver):
                    break
                _stop_sleep(0.5)
            if not (_has_strict_add_phone_form(driver) or _is_phone_code_page(driver)):
                raise RuntimeError("not_phone_flow")
        except Exception:
            logger.info("[Codex][Browser] 未检测到手机号验证页，跳过手机步骤")
            return

        sms_provider.preflight_sms_dependency(http=http)
        last_err = None
        for attempt in range(1, max_retries + 1):
            activation_id = None
            try:
                # 页面断连时先恢复 add-phone，再采购号码；否则会把新激活号
                # 立刻取消，连续消耗短信库存和重试次数。
                _ensure_add_phone_input(driver, reason=f"before-acquire-attempt-{attempt}")
                activation_id, phone = sms_provider.acquire_number(http)
                snapshot = {}
                try:
                    snapshot = sms_provider.get_activation_info(activation_id) or {}
                except Exception:
                    snapshot = {}
                price = " ".join(
                    part for part in (str(snapshot.get("price_amount") or ""), str(snapshot.get("price_currency") or "")) if part
                ) or "unknown"
                logger.info(
                    "[Codex][Browser] 手机验证尝试 %s/%s，provider=%s，号码=%s",
                    attempt, max_retries, provider, sms_provider._mask_phone(phone),
                )
                logger.info(
                    "[Codex][Browser] 号码采购快照：activation=%s region=%s provider_country=%s "
                    "price=%s price_limit_max=%s price_validated=%s",
                    activation_id, snapshot.get("phone_country_name") or snapshot.get("phone_region") or "unknown",
                    snapshot.get("country") or "unknown", price,
                    snapshot.get("price_limit_max") or "unlimited", snapshot.get("price_validated", False),
                )
                e164 = f"+{phone}"
                # 号码尝试只允许一次：任何未进入验证码页的失败都立即释放，
                # 不再用同一个激活反复提交，避免平台继续计费或状态失控。
                response_diagnostic = None
                response_watch = _start_add_phone_response_watch(driver)
                try:
                    _prepare_and_submit_add_phone(
                        driver, e164, label=f"-attempt-{attempt}-submit-1",
                    )
                    _wait_after_phone_send(driver, timeout=15)
                finally:
                    response_diagnostic = _finish_add_phone_response_watch(response_watch)
                logger.info("[Codex][Browser] 已进入手机验证码页")

                sms_provider.set_status(activation_id, 1, http=http)
                logger.info(
                    "[Codex][Browser] 短信已发送，开始轮询验证码 activation_id=%s wait=%ss interval=%ss",
                    activation_id, sms_provider._cfg.SMS_CODE_WAIT, sms_provider._cfg.SMS_POLL_INTERVAL
                )
                sms_code = sms_provider.wait_for_sms_code(activation_id, http)
                logger.info("[Codex][Browser] 手机 OTP 收到：length=%s", len(str(sms_code or "")))
                _install_phone_otp_validate_hook(driver)
                phone_otp_baseline = _phone_otp_validate_snapshot(driver).get("count", 0)
                phone_otp_info = _fill_phone_otp(driver, sms_code)
                logger.info("[Codex][Browser] 已填写手机 OTP：%s", phone_otp_info)
                auto_submitted = bool(phone_otp_info.get("auto_submitted"))
                if not auto_submitted:
                    auto_submitted = _wait_for_phone_otp_auto_submit(driver, phone_otp_baseline)
                if not auto_submitted:
                    human_delay("otp_input")
                    auto_submitted = _phone_otp_auto_submit_started(driver, phone_otp_baseline)
                if not auto_submitted:
                    if not _click_if_present(driver, ["button[type='submit']", "input[type='submit']"], timeout=10):
                        raise RuntimeError(f"verify_submit_missing: phone verification submit not found state={_phone_page_state(driver)}")
                else:
                    logger.info("[Codex][Browser] 手机 OTP 已由页面自动提交，不再重复点击")
                logger.info("[Codex][Browser] 已提交手机 OTP，等待验证结果")
                otp_outcome = _wait_after_phone_otp_submit(driver, timeout=25)
                logger.info("[Codex][Browser] 手机 OTP 提交后状态：%s", otp_outcome)
                if not _phone_otp_outcome_accepted(otp_outcome):
                    raise RuntimeError(f"phone_otp_not_accepted: state={otp_outcome}")
                sms_provider.report_success(activation_id)
                return sms_provider.complete(activation_id, http) or {}
            except (
                sms_provider.SmsNoBalanceError,
                sms_provider.SmsProviderConfigurationError,
                sms_provider.SmsNoNumbersError,
            ) as exc:
                last_err = exc
                if activation_id:
                    try:
                        sms_provider.cancel_and_report_failure(activation_id, http, exc)
                    except Exception as feedback_exc:
                        logger.warning("[Codex][Browser] 释放号码或记录短信依赖失败反馈失败：%s", feedback_exc)
                raise
            except Exception as exc:
                last_err = exc
                err_text = str(exc) or ""
                if response_diagnostic and response_diagnostic not in err_text:
                    err_text = f"{err_text}; add_phone_response={response_diagnostic}"
                    exc = RuntimeError(err_text)
                    last_err = exc
                logger.warning("[Codex][Browser] 手机验证尝试失败：%s", err_text[:420])
                if activation_id:
                    try:
                        sms_provider.cancel_and_report_failure(activation_id, http, exc)
                    except Exception as feedback_exc:
                        logger.warning("[Codex][Browser] 释放号码或记录短信失败反馈失败：%s", feedback_exc)
                # 余额不足 / 无可用号码：重试多少次都不会成功，立即失败止损，
                # 避免白等 N 轮换号重试（每轮还要刷新页面 + 随机等待）。
                if any(k in err_text for k in (
                    "NO_BALANCE", "NO_NUMBERS", "BALANCE", "余额不足",
                    "暂无可用号码", "没有可用号码", "insufficient", "not enough balance",
                )):
                    raise RuntimeError(
                        f"接码平台余额不足或无可用号码，已停止换号：{err_text[:180]}"
                    ) from exc
                if "invalid_auth_step" in str(exc):
                    raise RuntimeError(
                        "手机号流程进入 invalid_auth_step，说明授权状态还未从 email-verification 正常跳转或已失效；"
                        "已停止继续换号，避免继续消耗号码"
                    ) from exc
                if any(k in err_text for k in (
                    "phone_react_state_sync_failed", "phone_otp_input_sync_failed",
                    "whatsapp_channel_reverted",
                )):
                    logger.info(
                        "[Codex][Browser] 页面状态同步失败，已释放当前号码并按设置继续换号：attempt=%s/%s reason=%s",
                        attempt, max_retries, err_text[:180],
                    )
                if any(k in err_text for k in (
                    "phone_country_sync_failed", "phone_country_mismatch",
                    "phone_value_write_failed", "phone_value_mismatch", "phone_number_required",
                    "sms_channel_select_failed",
                )):
                    logger.info(
                        "[Codex][Browser] 手机号国家/表单反馈可重试：attempt=%s/%s reason=%s",
                        attempt, max_retries, err_text[:180],
                    )
                if attempt < max_retries:
                    try:
                        _refresh_add_phone_for_retry(driver, reason=str(exc)[:120])
                    except Exception as refresh_exc:
                        logger.warning(
                            "[Codex][Browser] 换号前刷新手机号页面失败，下一轮继续尝试恢复：%s",
                            str(refresh_exc)[:180],
                        )
                    _sleep_before_phone_retry(attempt, max_retries, reason=err_text)
        raise RuntimeError(f"Roxy 手机验证重试 {max_retries} 次仍失败，最后错误：{last_err}")
    finally:
        try:
            http.close()
        except Exception:
            pass


def _finish_consent_workspace(driver) -> str:
    """点击 Codex consent/workspace 页面里的继续/允许按钮，直到 callback。"""
    end = time.time() + int(_roxy_cfg.ROXY_CODEX_CALLBACK_TIMEOUT)
    while time.time() < end:
        callback = _extract_callback_url_from_any_window(driver)
        if callback:
            return callback
        current = str(driver.current_url or "")
        clicked = False
        for selectors in [
            ["//button[contains(., 'Allow')]", "//button[contains(., 'Authorize')]", "//button[contains(., 'Continue')]"],
            ["//button[contains(., 'Select')]", "//button[contains(., 'Use workspace')]", "//button[contains(., 'Confirm')]"],
            ["//button[contains(., '允许')]", "//button[contains(., '授权')]", "//button[contains(., '继续')]", "//button[contains(., '确认')]"],
            ["button[type='submit']"],
        ]:
            if _click_if_present(driver, selectors, timeout=2):
                clicked = True
                human_delay("form")
                break
        if not clicked:
            _stop_sleep(0.8)
    return _wait_for_callback(driver, timeout=5)




def clear_roxy_browser_auth_state(driver) -> None:
    """清空当前 Roxy 浏览器里的 OpenAI/ChatGPT 登录态与缓存，用于注册后复用同一环境跑 Codex。"""
    origins = [
        "https://auth.openai.com",
        "https://chatgpt.com",
        "https://openai.com",
        "https://platform.openai.com",
    ]
    logger.info("[Codex][Browser] 复用注册窗口：开始清理 Cookie / localStorage / sessionStorage / cache")
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
        logger.info("[Codex][Browser] 已清理浏览器 Cookie")
    except Exception as exc:
        logger.info("[Codex][Browser] 清理 Cookie 失败，继续尝试其它缓存：%s", str(exc)[:160])
    try:
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        logger.info("[Codex][Browser] 已清理浏览器 Cache")
    except Exception as exc:
        logger.info("[Codex][Browser] 清理 Cache 失败，继续：%s", str(exc)[:160])
    for origin in origins:
        try:
            driver.execute_cdp_cmd("Storage.clearDataForOrigin", {
                "origin": origin,
                "storageTypes": "all",
            })
            logger.info("[Codex][Browser] 已清理站点数据：%s", origin)
        except Exception as exc:
            logger.debug("[Codex][Browser] 清理站点数据失败 %s: %s", origin, exc)
    try:
        driver.get("about:blank")
    except Exception:
        pass
    _stop_sleep(1.0)
    logger.info("[Codex][Browser] 注册窗口登录态清理完成，准备开始 Codex 授权")

def _run_roxy_codex_oauth_once(
    email: str,
    otp_provider=None,
    proxy: str | None = None,
    force: bool = False,
    existing_driver=None,
    existing_opened=None,
    reuse_existing_profile: bool = False,
    clear_existing_state: bool = True,
    registration_password: str | None = None,
    _phone_activation: dict | None = None,
) -> dict:
    """指纹浏览器 Codex OAuth 入口。

    existing_driver/existing_opened 用于“注册成功后立刻跑 Codex”：
    复用注册时的 Roxy 窗口，不新建环境，只清理浏览器状态后开始授权。
    """
    from core import codex_oauth as proto

    if not force and not proto._cfg.ENABLE_CODEX_AUTO:
        return proto._codex_result(status="skipped", message="ENABLE_CODEX_AUTO=False")
    if not email:
        return proto._codex_result(status="skipped", message="email 为空")
    if otp_provider is None:
        otp_provider = wait_for_otp
    if registration_password:
        # 注册流程传下来的密码优先于查库结果，避免账号未落库时静默降级。
        remember_registration_password(email, registration_password)
    phone_activation = dict(_phone_activation or {})

    client = None if reuse_existing_profile else RoxyBrowserClient()
    try:
        opened = existing_opened if reuse_existing_profile else client.open_profile()
    except BaseException:
        if client is not None:
            client.close()
        raise
    browser_kind_token = _CODEX_BROWSER_KIND.set(_detect_browser_kind(opened))
    driver = existing_driver if reuse_existing_profile else None
    owns_driver = not reuse_existing_profile
    try:
        auth_source = proto._codex_auth_url_source()
        code_verifier = None
        if auth_source == "cpa":
            cpa_auth = proto._request_cpa_authorize_url()
            state = cpa_auth["state"]
            auth_url = cpa_auth["auth_url"]
            logger.info("[Codex][Browser] 当前使用 CPA 授权地址: %s", auth_url)
        elif auth_source == "sub2":
            sub2_auth = proto._request_sub2_authorize_url()
            state = sub2_auth["state"]
            auth_url = sub2_auth["auth_url"]
            logger.info("[Codex][Browser] 当前使用 sub2 授权地址: %s", auth_url)
        elif auth_source == "local":
            code_verifier, code_challenge = proto._generate_pkce()
            state = proto._generate_state()
            auth_url = proto._build_authorize_url(state, code_challenge, prompt="login")
            logger.info("[Codex][Browser] 当前使用本地 PKCE 授权地址: %s", auth_url)
        else:
            raise RuntimeError(f"[Codex][Browser] 不支持的 CODEX_AUTH_URL_SOURCE={auth_source!r}")

        if not driver:
            driver = _build_driver(opened)
            _center_browser_window(driver)
        driver.set_page_load_timeout(int(_roxy_cfg.ROXY_SELENIUM_TIMEOUT))
        logger.info("[Codex][Browser] 开始授权：%s，profile=%s，reuse_existing_profile=%s", email, opened.profile_id, reuse_existing_profile)
        if reuse_existing_profile and clear_existing_state:
            clear_roxy_browser_auth_state(driver)

        _fill_email_and_otp(
            driver, email, otp_provider, auth_url, registration_password=registration_password
        )
        human_delay("api")
        logger.info("[Codex][Browser] 检查是否需要手机号验证")
        current_phone_activation = _do_phone_verification_if_present(driver) or {}
        phone_activation = proto._merge_phone_activation(
            phone_activation, current_phone_activation
        )
        logger.info("[Codex][Browser] 手机验证处理完成/无需处理，等待授权确认和 callback")
        callback_url = _finish_consent_workspace(driver)
        code = proto._extract_code(callback_url, state)
        logger.info("[Codex][Browser] 已捕获 callback code：%s...", code[:24])

        if auth_source == "cpa":
            submit_payload = proto._submit_cpa_callback(callback_url)
            path = proto._save_cpa_local_record(
                email=email,
                callback_url=callback_url,
                auth_url=auth_url,
                state=state,
                submit_payload=submit_payload,
                phone_activation=phone_activation,
            )
            msg = submit_payload.get("message") or submit_payload.get("status_message") or "CPA callback submitted"
            return proto._codex_result(
                status="success",
                ok=True,
                email=email,
                file_path=str(path) if path else None,
                callback_url=callback_url,
                message=f"{_codex_driver_name()}: {msg}",
                phone_activation=phone_activation,
            )

        if auth_source == "sub2":
            submit_payload = proto._submit_sub2_callback(
                callback_url,
                session_id=(sub2_auth or {}).get("session_id", ""),
                redirect_uri=(proto.parse_qs(proto.urlparse(auth_url or "").query).get("redirect_uri") or [""])[0],
            )
            path = proto._save_sub2_local_record(
                email=email,
                callback_url=callback_url,
                auth_url=auth_url,
                state=state,
                submit_payload=submit_payload,
                phone_activation=phone_activation,
            )
            msg = submit_payload.get("message") or submit_payload.get("status_message") or "sub2 callback uploaded"
            return proto._codex_result(
                status="success",
                ok=True,
                email=email,
                file_path=str(path) if path else None,
                callback_url=callback_url,
                message=f"{_codex_driver_name()}: {msg}",
                phone_activation=phone_activation,
            )

        if not code_verifier:
            raise RuntimeError("[Codex][Browser] local 模式缺少 code_verifier")
        session = proto.BrowserSession(proxy=proxy, fingerprint_seed=f"account:{email.lower()}")
        try:
            token_resp = proto.exchange_codex_token(session, code, code_verifier)
        finally:
            proto.close_browser_session(session)
        id_claims = proto._parse_id_token(token_resp.get("id_token", ""))
        effective_email = id_claims.get("email") or email
        storage = proto.build_codex_storage(token_resp, id_claims)
        path = proto.save_codex_credential(
            storage,
            effective_email,
            id_claims.get("plan_type", ""),
            phone_activation=phone_activation,
        )
        return proto._codex_result(
            status="success",
            ok=True,
            email=effective_email,
            file_path=str(path),
            callback_url=callback_url,
            message=f"{_codex_driver_name()} plan={id_claims.get('plan_type') or 'unknown'}",
            phone_activation=phone_activation,
        )
    except AccountUnusableError as exc:
        logger.warning("[Codex][Browser] 账号已废：%s，%s", email, exc.error_code)
        return proto._codex_result(
            status="deactivated",
            email=email,
            message=f"账号已废（{exc.error_code or 'account_deactivated'}）",
            phone_activation=phone_activation,
        )
    except Exception as exc:
        logger.warning("[Codex][Browser] 失败：%s，%s: %s", email, type(exc).__name__, str(exc)[:240])
        logger.debug("[Codex][Browser] 失败详情", exc_info=True)
        if isinstance(exc, sms_provider.SmsProviderError):
            sms_outcome = sms_provider.classify_sms_exception(exc)
            return proto._codex_result(
                status=sms_outcome["status"],
                email=email,
                error_code=sms_outcome["error_code"],
                retryable=sms_outcome["retryable"],
                message=sms_outcome["message"][:240],
                phone_activation=phone_activation,
            )
        return proto._codex_result(
            status="failed",
            email=email,
            message=f"{type(exc).__name__}: {str(exc)[:220]}",
            phone_activation=phone_activation,
        )
    finally:
        # 注册后复用窗口时，driver/profile 生命周期由注册流程统一清理，
        # 这里不能 quit/delete，否则会提前销毁注册环境。
        if owns_driver and driver and not bool(_roxy_cfg.ROXY_KEEP_BROWSER_OPEN):
            _bounded_stop_cleanup("codex.driver.quit", driver.quit, always=True)
        if owns_driver and client:
            if not bool(_roxy_cfg.ROXY_KEEP_BROWSER_OPEN):
                try:
                    _bounded_stop_cleanup("codex.client.cleanup_profile", lambda: client.cleanup_profile(opened))
                finally:
                    client.close()
            else:
                client.close(keep_proxy_relay=True)
        try:
            _CODEX_BROWSER_KIND.reset(browser_kind_token)
        except Exception:
            pass


def run_roxy_codex_oauth(
    email: str,
    otp_provider=None,
    proxy: str | None = None,
    force: bool = False,
    existing_driver=None,
    existing_opened=None,
    reuse_existing_profile: bool = False,
    clear_existing_state: bool = True,
    registration_password: str | None = None,
) -> dict:
    """指纹浏览器 Codex OAuth 入口；CPA callback 409 timeout 时重新开启一轮授权。

    registration_password 用于注册后立刻跑 Codex 的场景：账号此时尚未落库，
    Codex 登录密码页拿不到密码就会降级成一次性验证码登录并被服务端拒绝。
    """
    from core import codex_oauth as proto

    if registration_password:
        remember_registration_password(email, registration_password)

    max_rounds = 2
    last_result = None
    phone_activation: dict = {}
    for round_no in range(1, max_rounds + 1):
        if round_no > 1:
            logger.warning(
                "[Codex][Browser] CPA callback 返回 Timeout waiting for OAuth callback，重新开启第 %s/%s 轮 Codex 授权：%s",
                round_no, max_rounds, email,
            )
        result = _run_roxy_codex_oauth_once(
            email=email,
            otp_provider=otp_provider,
            proxy=proxy,
            force=force,
            existing_driver=existing_driver,
            existing_opened=existing_opened,
            reuse_existing_profile=reuse_existing_profile,
            clear_existing_state=clear_existing_state,
            registration_password=registration_password,
            _phone_activation=phone_activation,
        )
        phone_activation = proto._merge_phone_activation(
            phone_activation, result.get("phone_activation")
        )
        if phone_activation:
            result = dict(result)
            result["phone_activation"] = phone_activation
        last_result = result
        if result.get("ok"):
            return result
        msg = result.get("message") or result.get("error") or ""
        if not proto._is_cpa_callback_reauth_error(msg):
            return result
    if last_result:
        last_result = dict(last_result)
        last_result["message"] = f"CPA callback 超时，已重新授权 {max_rounds} 轮仍失败：{last_result.get('message') or ''}"
        return last_result
    return proto._codex_result(
        status="failed",
        email=email,
        message="CPA callback 超时，重新授权失败",
        phone_activation=phone_activation,
    )
