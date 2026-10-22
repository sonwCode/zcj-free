# -*- coding: utf-8 -*-
"""已注册账号查活：优先复用已有 AT 预热后走 reauth OTP，成功刷新 AT 即视为正常。"""
import logging
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from core import db
from core.session import BrowserSession, close_browser_session
from core.codex_oauth import _account_registration_password, _account_totp_secret, _account_totp_code
from core.humanize import delay as human_delay
from core.chatgpt_auth import get_csrf_token, get_providers, probe_auth_session, signin_openai
from core.openai_auth import (
    follow_authorize,
    send_email_otp,
    validate_email_otp,
    EmailOtpInvalidError,
    AccountUnusableError,
    detect_account_unusable_text,
)
from core.account_export import (
    _follow_reauth_with_retry,
    _trigger_reauth_with_retry,
    _validate_reauth_otp,
    fetch_session,
    follow_oauth_callback,
)
from core.email_provider import wait_for_otp
from core.http_diagnostics import is_cloudflare_challenge, response_summary

logger = logging.getLogger(__name__)
_LOG_DIR = Path(__file__).resolve().parent.parent / "注册日志"
_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()

# 查活网络预检失败（403/429/代理/超时等）多为出口 IP 被 CF 标记或代理池抖动，
# 视为可换新 IP 重试；账号本身问题（废号/邮箱错误等）不重试。
_RETRYABLE_NETWORK_HINTS = (
    "403", "429", "502", "503", "504",
    "proxy", "socks", "timeout", "timed out",
    "connection", "closed", "reset",
)

_SESSION_FINGERPRINT_KEYS = {
    "device_id",
    "sentinel_sid",
    "oai_session_id",
    "auth_session_logging_id",
    "datadog_trace_id",
    "datadog_parent_id",
    "react_listening_key",
    "react_container_key",
    "react_resources_key",
}


class CloudflareChallengeError(RuntimeError):
    """An upstream Cloudflare challenge, not evidence that the account is dead."""

    error_code = "cloudflare_challenge"
    retryable = True

    def __init__(self, response):
        self.response = response
        super().__init__(f"Cloudflare challenge: {response_summary(response)}")


def _challenge_error_from_exception(exc: BaseException) -> CloudflareChallengeError | None:
    response = getattr(exc, "response", None)
    if response is None or not is_cloudflare_challenge(response):
        return None
    return CloudflareChallengeError(response)


def _is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, AccountUnusableError):
        return False
    text = str(exc or "").lower()
    return any(h in text for h in _RETRYABLE_NETWORK_HINTS)


def _new_fingerprint_pinned_session(
    email: str,
    proxy: str | None,
    fingerprint_state: dict | None = None,
) -> BrowserSession:
    """创建任务独占账号会话；同一路由尝试内固定完整身份与浏览器画像。"""
    state = fingerprint_state if fingerprint_state is not None else {}
    saved_profile = state.get("browser_profile")
    identity = str(email).strip().lower()
    # 和协议注册使用同一套生命周期配置：开启“同邮箱保持协议指纹”时，
    # 查活可重新构造注册阶段的 device/session/硬件画像；关闭时每次查活
    # 生成独立 seed。无论哪种模式，同一任务内部的所有阶段/重试都固定。
    fingerprint_seed = str(state.get("fingerprint_seed") or "").strip() or None
    if not state.get("fingerprint_initialized"):
        from config import register as register_cfg
        reuse_by_email = bool(
            getattr(register_cfg, "PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL", False)
        ) and not bool(state.get("force_fresh"))
        # “每次重新创建”必须和协议注册完全一致：不传 seed，让 BrowserSession
        # 生成真实的随机 UUID4。旧实现先生成随机 seed 再派生 UUID5，虽然值也
        # 随机，但 UUID version 位与注册时不同，属于可观测的指纹差异。
        fingerprint_seed = f"registration:{identity}" if reuse_by_email else None
        state["fingerprint_seed"] = fingerprint_seed or ""
        state["fingerprint_mode"] = (
            "registration_email_stable" if reuse_by_email else "fresh_per_check"
        )
        state["fingerprint_initialized"] = True
    session = BrowserSession(
        proxy=proxy,
        # 首次按当前出口生成地区画像；同一路由内部如需重建则原样复用。
        detect_exit_geo=not bool(saved_profile),
        browser_profile=dict(saved_profile) if isinstance(saved_profile, dict) else None,
        fingerprint_seed=fingerprint_seed,
        device_id=state.get("device_id"),
        auth_session_logging_id=state.get("auth_session_logging_id"),
        oai_session_id=state.get("oai_session_id"),
        sentinel_sid=state.get("sentinel_sid"),
    )
    for key in (
        "device_id", "auth_session_logging_id", "oai_session_id", "sentinel_sid",
    ):
        state.setdefault(key, str(getattr(session, key, "") or ""))
    if not saved_profile:
        generated_profile = getattr(session, "browser_profile", None)
        if isinstance(generated_profile, dict):
            state["browser_profile"] = dict(generated_profile)
    return session


def _fingerprint_mode_label(state: dict) -> str:
    mode = str(state.get("fingerprint_mode") or "")
    if mode == "registration_email_stable":
        return "同邮箱复用协议注册指纹"
    return "每次查活重新创建"


def _warm_login_fingerprint_context(session: BrowserSession) -> None:
    """复现 plus 纯协议注册成功样本的登录页初始化顺序。"""
    from core.chatgpt_bootstrap import anonymous_bootstrap

    logger.info(
        "[查活] 登录链预热：/auth/login 顶层导航 → anonymous bootstrap → "
        "providers → session → CSRF → session"
    )
    nav = session.get(
        "https://chatgpt.com/auth/login",
        headers=session.get_chatgpt_navigate_headers(
            # 地址栏级顶层导航：无 Referer，Sec-Fetch-Site=none。
            referer="", user_initiated=True,
        ),
        allow_redirects=True,
        # 代理端口可连接不代表其上游 TLS 可用，避免坏节点长期占住 worker。
        timeout=12,
    )
    try:
        nav.raise_for_status()
    except Exception as exc:
        challenge = _challenge_error_from_exception(exc)
        if challenge is not None:
            logger.warning("[查活] 登录页被 Cloudflare challenge 拦截：%s", challenge)
            raise challenge from exc
        raise
    observe = getattr(session, "observe_chatgpt_document", None)
    if callable(observe):
        observe(nav)
    anonymous_bootstrap(session, strict=False)
    # best-effort bootstrap 的非关键接口不能阻断正式认证链。
    _clear_optional_bootstrap_circuit(session)
    get_providers(session)
    probe_auth_session(session)


def _network_preflight_with_retry(
    email: str,
    proxy: str | None,
    max_attempts: int = 4,
    fingerprint_state: dict | None = None,
) -> tuple[BrowserSession, str]:
    """CSRF → Signin 备用预检；失败时保留同一会话重试。

    `/api/auth/providers` 只是 NextAuth 的发现接口，signin 端点并不依赖它返回的
    内容。实际运行中该接口很容易先被 Cloudflare 拦截，如果把它作为硬门槛，后续
    本来可用的 CSRF/授权链永远不会执行。因此查活备用链不再把 providers 当作
    必经步骤。

    这里必须原样传递 ``proxy``：``None`` 表示按配置选代理，空字符串表示明确
    直连。之前用 ``proxy if proxy else None`` 把直连兜底误变成了再次抽取代理。
    """
    session: BrowserSession | None = None
    last_exc: BaseException | None = None
    state = fingerprint_state if fingerprint_state is not None else {}
    # 一次网络预检只创建一个 BrowserSession。403 响应下发的新 __cf_bm、
    # OAuth/设备上下文都保留在同一 Cookie Jar 中供下一轮使用。
    session = _new_fingerprint_pinned_session(email, proxy, state)
    logger.info("[查活] 指纹生命周期：%s", _fingerprint_mode_label(state))
    for attempt in range(1, max_attempts + 1):
        logger.info(
            "[查活] 复用统一会话：proxy=%s device_id=%s oai_session_id=%s（网络预检第 %s/%s 次）",
            session.proxy or "配置随机/直连", session.device_id,
            str(getattr(session, "oai_session_id", "") or "")[:12] + "...",
            attempt, max_attempts,
        )
        logger.info("[查活] 指纹摘要：%s", session.fingerprint_summary_text())
        try:
            _warm_login_fingerprint_context(session)
            csrf = get_csrf_token(session)
            # 成功 Web 样本在 signin 前会再次确认匿名 NextAuth session。
            probe_auth_session(session)
            authorize_url = signin_openai(session, csrf, email)
            return session, authorize_url
        except Exception as exc:
            last_exc = exc
            challenge = _challenge_error_from_exception(exc)
            if challenge is not None:
                try:
                    close_browser_session(session)
                except Exception:
                    pass
                logger.warning(
                    "[查活] 当前出口收到 Cloudflare challenge，停止同会话重试并切换出口：%s",
                    challenge,
                )
                raise challenge from exc
            # A 403 from the ChatGPT/Cloudflare preflight is an exit-level
            # block for this proxy session. Retrying the same BrowserSession
            # only replays the blocked request and delays the outer route
            # switch, which made a batch look like every account had failed.
            if "403" in str(exc or ""):
                try:
                    close_browser_session(session)
                except Exception:
                    pass
                logger.warning(
                    "[查活] 网络预检收到 HTTP 403，立即交给上层切换代理：%s",
                    str(exc)[:200],
                )
                raise
            if attempt >= max_attempts or not _is_retryable_network_error(exc):
                try:
                    close_browser_session(session)
                except Exception:
                    pass
                raise
            _clear_optional_bootstrap_circuit(session)
            logger.warning(
                "[查活] 网络预检失败（%s/%s），保留当前 session/deviceId/CF Cookie 重试：%s",
                attempt, max_attempts, str(exc)[:200],
            )
            time.sleep(2)
    raise RuntimeError(f"网络预检多次失败：{last_exc}")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_fingerprint_for_account(session: BrowserSession) -> dict:
    """账号里只记录运行环境画像，不保存会话/设备标识。"""
    fp = session.fingerprint_summary()
    return {k: v for k, v in fp.items() if k not in _SESSION_FINGERPRINT_KEYS}


def _safe_fingerprint_text_for_account(session: BrowserSession) -> str:
    fp = _safe_fingerprint_for_account(session)
    parts = [
        f"proxy={BrowserSession._short_value(fp.get('proxy') or 'direct', 36)}",
        f"ua={BrowserSession._short_value(fp.get('user_agent'), 72)}",
        f"lang={fp.get('accept_language')}",
        f"tz={fp.get('timezone_iana')}({fp.get('timezone_offset_minutes')})",
        f"screen={fp.get('screen_width')}x{fp.get('screen_height')}@{fp.get('device_pixel_ratio')}",
        f"cpu={fp.get('hardware_concurrency')}",
        f"mem={fp.get('device_memory')}",
        f"geo={fp.get('geo_country') or '?'}:{fp.get('geo_city') or '?'}",
    ]
    return " ".join(parts)


def _extract_continue_url(result: dict | None) -> str:
    if not isinstance(result, dict):
        return ""
    page = result.get("page") or {}
    page = page if isinstance(page, dict) else {}
    return str(
        result.get("continue_url")
        or result.get("external_url")
        or result.get("url")
        or page.get("continue_url")
        or page.get("external_url")
        or page.get("url")
        or ""
    ).strip()


def _extract_factor_id(result: dict | None, continue_url: str) -> str:
    if isinstance(result, dict):
        page = result.get("page") or {}
        page = page if isinstance(page, dict) else {}
        payload = page.get("payload") or {}
        if isinstance(payload, dict):
            factor_id = str(payload.get("factor_id") or "").strip()
            if factor_id:
                return factor_id
        if isinstance(page.get("payload"), dict):
            factor_id = str(page["payload"].get("factor_id") or "").strip()
            if factor_id:
                return factor_id
    if "/mfa-challenge/" in continue_url:
        return continue_url.rstrip("/").rsplit("/", 1)[-1]
    return ""


_PWD_MISMATCH_PATH = Path(__file__).resolve().parent.parent / "data" / "password_mismatch.json"


def _password_mismatch_known(email: str) -> bool:
    """该账号是否已被确认"本地密码与账号不一致"。"""
    try:
        if not _PWD_MISMATCH_PATH.exists():
            return False
        data = json.loads(_PWD_MISMATCH_PATH.read_text(encoding="utf-8"))
        return str(email or "").strip().lower() in {k.lower() for k in data}
    except Exception:
        return False


def _remember_password_mismatch(email: str, status: int) -> None:
    """记住密码失效的账号，后续直接走邮箱 OTP，不再白试一次。"""
    try:
        _PWD_MISMATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if _PWD_MISMATCH_PATH.exists():
            try:
                data = json.loads(_PWD_MISMATCH_PATH.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        data[str(email or "").strip().lower()] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": int(status),
        }
        _PWD_MISMATCH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        logger.debug("[查活] 记录密码失效清单失败", exc_info=True)


def _password_verify(session: BrowserSession, password: str) -> dict:
    from core.openai_auth import build_sentinel_header, request_sentinel_token

    sentinel_resp = request_sentinel_token(session, "password_verify")
    sentinel_header, so_header = build_sentinel_header(session, sentinel_resp, "password_verify")
    headers = session.get_auth_headers(referer="https://auth.openai.com/log-in/password")
    headers["openai-sentinel-token"] = sentinel_header
    if so_header:
        headers["openai-sentinel-so-token"] = so_header
    resp = session.post(
        "https://auth.openai.com/api/accounts/password/verify",
        headers=headers,
        data=json.dumps({"password": password}),
        allow_redirects=False,
    )
    if resp.status_code in (400, 401, 403):
        # 本地保存的密码与账号上的密码不一致（早期注册的密码存在输入串码 bug）。
        # 这不是账号失效，也不是网络问题，交给上层回退邮箱 OTP。
        body = ""
        try:
            body = str(resp.text or "")
        except Exception:
            body = ""
        logger.warning(
            "[查活] 密码校验被拒：status=%s body=%s", resp.status_code, body[:160],
        )
        raise RuntimeError(
            f"password_verify_failed: status={resp.status_code} body={body[:160]}"
        )
    resp.raise_for_status()
    return resp.json()


def _mfa_issue_challenge(session: BrowserSession, factor_id: str) -> dict:
    headers = session.get_auth_headers(referer="https://auth.openai.com/mfa-challenge")
    headers.pop("openai-sentinel-token", None)
    headers.pop("openai-sentinel-so-token", None)
    resp = session.post(
        "https://auth.openai.com/api/accounts/mfa/issue_challenge",
        headers=headers,
        data=json.dumps({"id": factor_id, "type": "totp", "force_fresh_challenge": False}),
        allow_redirects=False,
    )
    resp.raise_for_status()
    return resp.json()


def _mfa_verify(session: BrowserSession, factor_id: str, code: str) -> dict:
    headers = session.get_auth_headers(referer="https://auth.openai.com/mfa-challenge")
    headers.pop("openai-sentinel-token", None)
    headers.pop("openai-sentinel-so-token", None)
    resp = session.post(
        "https://auth.openai.com/api/accounts/mfa/verify",
        headers=headers,
        data=json.dumps({"id": factor_id, "type": "totp", "code": code}),
        allow_redirects=False,
    )
    resp.raise_for_status()
    return resp.json()


def _follow_continue_and_fetch(session: BrowserSession, continue_url: str, *, referer: str) -> dict:
    """完成 callback/session，并对 403 保留同会话 Cookie 做阶段内重试。

    callback 与 session 分开重试：callback 一旦成功就不重复消费 OAuth code；
    只有 callback 本身失败时才重放 continue_url。重试耗尽后抛给上层，由
    live_check_service 按既有策略换成独立直连会话完整兜底。
    """
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            follow_oauth_callback(session, continue_url, referer=referer)
            break
        except Exception as exc:
            if attempt >= max_attempts or not _is_retryable_network_error(exc):
                raise
            _clear_optional_bootstrap_circuit(session)
            delay = float(2 ** (attempt - 1))
            logger.warning(
                "[查活] OAuth callback 临时失败（%s/%s），保留当前 "
                "session/deviceId/CF Cookie，%.1fs 后重试：%s",
                attempt, max_attempts, delay, str(exc)[:200],
            )
            time.sleep(delay)

    for attempt in range(1, max_attempts + 1):
        try:
            return fetch_session(session)
        except Exception as exc:
            if attempt >= max_attempts or not _is_retryable_network_error(exc):
                raise
            _clear_optional_bootstrap_circuit(session)
            delay = float(2 ** (attempt - 1))
            logger.warning(
                "[查活] Session/AT 拉取临时失败（%s/%s），保留当前 "
                "session/deviceId/CF Cookie，%.1fs 后重试：%s",
                attempt, max_attempts, delay, str(exc)[:200],
            )
            time.sleep(delay)
    raise RuntimeError("查活 Session/AT 拉取重试耗尽")


def _stored_access_token(email: str) -> str:
    """读取本地账号已有 AT，用于先预热登录态再走稳定的 reauth 链。"""
    try:
        account = db.get_account_by_email(email)
        return str((account or {}).get("access_token") or "").strip()
    except Exception as exc:
        logger.debug("[查活] 读取已有 accessToken 失败，改走备用登录链：%s: %s", type(exc).__name__, exc)
        return ""


def _probe_existing_access_token(email: str, access_token: str) -> dict | None:
    """通过只读 usage 接口快速确认现有 AT，避免页面 403 误判账号失败。"""
    token = str(access_token or "").strip()
    if not token:
        return None
    try:
        from curl_cffi.requests import Session as CurlSession
        from config import IMPERSONATE, USER_AGENT

        http = CurlSession(impersonate=IMPERSONATE)
        try:
            response = http.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                    "Origin": "https://chatgpt.com",
                    "Referer": "https://chatgpt.com/",
                },
                timeout=15,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status != 200:
                logger.info("[查活] 现有 AT 快速校验未通过 HTTP %s，继续完整查活：%s", status, email)
                return None
            try:
                payload = response.json()
            except Exception:
                payload = {}
            payload = payload if isinstance(payload, dict) else {}
            user_id = str(payload.get("user_id") or "").strip()
            plan_type = str(payload.get("plan_type") or "").strip()
            logger.info(
                "[查活] 现有 AT 快速校验成功，跳过页面登录/OTP：%s user_id=%s plan=%s",
                email,
                user_id or "-",
                plan_type or "-",
            )
            return {
                "ok": True,
                "status": "live",
                "checked_at": _now(),
                "access_token": token,
                "session": {
                    "accessToken": token,
                    "user": {"id": user_id, "email": str(payload.get("email") or email)},
                    "account": {"planType": plan_type} if plan_type else {},
                },
                "token_probe": "backend-api/wham/usage",
            }
        finally:
            http.close()
    except Exception as exc:
        logger.info(
            "[查活] 现有 AT 快速校验异常，继续完整查活：%s %s: %s",
            email,
            type(exc).__name__,
            str(exc)[:180],
        )
        return None


def _clear_optional_bootstrap_circuit(session: BrowserSession) -> None:
    """清理可选登录态预热造成的本地熔断，不影响后续正式认证请求。

    authenticated_bootstrap 是 best-effort 预热，其中个别旧接口返回 403 不等于
    reauth 链不可用；BrowserSession 的通用熔断器若保留该状态，会直接拦截后续
    `/api/auth/csrf`，导致稳定的 2FA 链也无法开始。
    """
    reset = getattr(session, "reset_circuit_breaker", None)
    if callable(reset):
        reset()
        return
    if getattr(session, "blocked_until", 0.0):
        session.blocked_until = 0.0
        session.blocked_reason = ""


def _warm_authenticated_session(session: BrowserSession, access_token: str) -> None:
    """复用 2FA 已验证的登录态预热流程。预热失败不直接判定账号死亡。"""
    if not access_token:
        return
    from core.chatgpt_bootstrap import authenticated_bootstrap

    try:
        logger.info("[查活] 使用已有 accessToken 预热登录态...")
        authenticated_bootstrap(session, access_token, strict=False)
        logger.info("[查活] accessToken 预热完成，继续走 reauth OTP")
    except Exception as exc:
        # strict=False 已经会吞掉大部分单接口错误；这里仅兜住初始化异常。
        logger.warning("[查活] accessToken 预热失败，继续走 reauth OTP：%s: %s", type(exc).__name__, str(exc)[:180])
    finally:
        _clear_optional_bootstrap_circuit(session)


def _exception_response_text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    return str(getattr(response, "text", "") or "")


def _exception_status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return None
    return status or None


def _is_otp_not_sent(exc: BaseException) -> bool:
    """"服务端没发验证码"：邮箱可读但无新邮件。

    与 otp_timeout（我们取不到）区分开：这类重试只会再等一轮并且更容易触发风控，
    所以直接判失败、不重试、不切换到干净会话。
    """
    code = str(getattr(exc, "error_code", "") or "").lower()
    if code == "otp_not_sent":
        return True
    return "otp_not_sent" in str(exc or "").lower()


def _is_otp_wait_timeout(exc: BaseException) -> bool:
    """取码阶段"等待验证码超时"（没有 HTTP 响应）属于可重试的瞬时问题。

    取码源在投递抖动时往往先超时、几十秒后验证码才到；此前只把 400/401/422
    视为可重试，导致日志写着"第 1/3 次"却第一次就放弃。
    """
    if _exception_status_code(exc) is not None:
        return False
    text = str(exc or "").lower()
    return ("超时" in str(exc or "")) or ("timeout" in text) or ("timed out" in text)


def _validate_reauth_with_retry(
    session: BrowserSession,
    email: str,
    otp_after_ts: float,
    max_otp_attempts: int = 3,
    email_source: str | None = None,
) -> str:
    """提交 reauth OTP；验证码错误时重新发送并重新取码。"""
    current_otp: str | None = None
    last_exc: Exception | None = None
    for attempt in range(1, max_otp_attempts + 1):
        try:
            if current_otp is None:
                logger.info("[查活] 等待重认证 OTP：%s（第 %s/%s 次）", email, attempt, max_otp_attempts)
                current_otp = wait_for_otp(
                    email,
                    after_ts=otp_after_ts,
                    email_source=email_source,
                )
            human_delay("otp_input")
            continue_url = _validate_reauth_otp(session, current_otp)
            if not continue_url:
                raise RuntimeError("重认证 OTP 验证响应缺少 continue_url")
            return str(continue_url)
        except AccountUnusableError:
            raise
        except Exception as exc:
            last_exc = exc
            body = _exception_response_text(exc)
            dead_code = detect_account_unusable_text(body) or detect_account_unusable_text(str(exc))
            if dead_code:
                raise AccountUnusableError(
                    f"账号已废弃（{dead_code}），邮箱不可再用",
                    error_code=dead_code,
                ) from exc

            if _is_otp_not_sent(exc):
                logger.warning("[查活] 服务端未发送验证码（otp_not_sent），不再重试：%s", email)
                raise
            status = _exception_status_code(exc)
            # reauth validate 的 403 可能是 Cloudflare/出口拦截；不要在同一已熔断
            # 会话上反复发送 OTP，交给上层的直连兜底处理。
            retryable_otp = status in (400, 401, 422) or _is_otp_wait_timeout(exc)
            if attempt >= max_otp_attempts or not retryable_otp:
                raise
            logger.warning(
                "[查活] 重认证 OTP 无效/过期，重新发送后再取（%s/%s）：%s",
                attempt,
                max_otp_attempts,
                str(exc)[:180],
            )
            send_email_otp(session)
            otp_after_ts = time.time()
            current_otp = None
            time.sleep(1)
    raise last_exc if last_exc else RuntimeError("重认证 OTP 验证失败")


def _login_via_reauth(
    session: BrowserSession,
    email: str,
    otp_after_ts: float,
    email_source: str | None = None,
) -> dict:
    """按 2FA 已验证链路重新认证并刷新 ChatGPT session。"""
    auth_url = _trigger_reauth_with_retry(session, email)
    logger.info("[查活] reauth authorize URL 已获取")
    human_delay("api")
    final_url = _follow_reauth_with_retry(session, auth_url)
    logger.info("[查活] reauth 落点页面：%s", str(final_url or "")[:200])
    dead_code = detect_account_unusable_text(final_url)
    if dead_code:
        raise AccountUnusableError(f"账号已废弃（{dead_code}）", error_code=dead_code)
    # 只有真正落到邮箱验证码页才会发新邮件。此前不校验落点，落到密码/TOTP/
    # 错误页时仍死等 90 秒，最终把"根本没发信"误报成"收件箱为空"。
    lowered = str(final_url or "").lower()
    if not any(k in lowered for k in ("email-verification", "email_verification")):
        stage = next(
            (label for key, label in (
                ("totp", "TOTP 动态码"), ("authenticator", "TOTP 动态码"),
                ("/mfa", "多因素认证"), ("/add-phone", "绑定手机号"),
                ("/log-in/password", "密码登录"), ("/about-you", "资料填写"),
                ("deactivated", "账号已停用"), ("error=", "错误页"),
            ) if key in lowered),
            "未知页面",
        )
        raise RuntimeError(
            f"重认证未进入邮箱验证页（落点={stage}，url={str(final_url or '')[:160]}），"
            "服务端不会发送新的邮箱验证码"
        )
    human_delay("navigate")
    logger.info("[查活] 已跟随 reauth authorize URL，开始等待邮箱 OTP")
    continue_url = _validate_reauth_with_retry(
        session,
        email,
        otp_after_ts,
        email_source=email_source,
    )
    logger.info("[查活] reauth OTP 验证通过，开始交换新 token")
    human_delay("api")
    return _follow_continue_and_fetch(
        session,
        continue_url,
        referer="https://auth.openai.com/email-verification",
    )


def _login_via_email_otp(
    session: BrowserSession,
    email: str,
    otp_after_ts: float,
    email_source: str | None = None,
) -> dict:
    """完成邮箱 OTP 登录，并跟随 OAuth callback 后拉取 ChatGPT session。"""
    validate_result = _validate_with_retry(
        session,
        email,
        otp_after_ts,
        email_source=email_source,
    )
    page = validate_result.get("page") if isinstance(validate_result, dict) else {}
    page = page if isinstance(page, dict) else {}
    page_type = str(page.get("type") or "")
    continue_url = _extract_continue_url(validate_result)
    if not continue_url:
        raise RuntimeError(f"OTP 登录成功但没有 OAuth continue_url: {validate_result}")
    if "about-you" in str(continue_url) or page_type in {"about_you", "about-you"}:
        raise RuntimeError(f"该邮箱登录后进入资料页，疑似不是完整已注册账号: page_type={page_type}, continue_url={continue_url}")
    logger.info("[查活] 邮箱 OTP 验证完成，开始跟随 OAuth callback")
    return _follow_continue_and_fetch(session, continue_url, referer="https://auth.openai.com/email-verification")


def _login_via_password_or_otp(
    session: BrowserSession,
    email: str,
    otp_after_ts: float,
    email_source: str | None = None,
) -> dict:
    """优先密码登录；如进入 MFA challenge 则自动用 TOTP 完成。"""
    password = _account_registration_password(email)
    if not password:
        logger.info("[查活] 未找到注册密码，继续使用邮箱 OTP：%s", email)
        return _login_via_email_otp(
            session,
            email,
            otp_after_ts,
            email_source=email_source,
        )

    if _password_mismatch_known(email):
        logger.warning("[查活] 该账号本地密码已知与账号不一致，直接走邮箱 OTP：%s", email)
        return _login_via_email_otp(
            session,
            email,
            otp_after_ts,
            email_source=email_source,
        )
    logger.info("[查活] 账号存在密码，优先走密码登录：%s", email)
    try:
        password_result = _password_verify(session, password)
    except RuntimeError as pwd_exc:
        if "password_verify_failed" not in str(pwd_exc):
            raise
        _remember_password_mismatch(email, 401)
        logger.warning(
            "[查活] 本地密码与账号不一致，回退邮箱 OTP：%s %s", email, str(pwd_exc)[:160],
        )
        return _login_via_email_otp(
            session,
            email,
            otp_after_ts,
            email_source=email_source,
        )
    continue_url = _extract_continue_url(password_result)
    page = password_result.get("page") if isinstance(password_result, dict) else {}
    page = page if isinstance(page, dict) else {}
    page_type = str(page.get("type") or "")

    if "/mfa-challenge/" in continue_url or page_type == "mfa_challenge":
        factor_id = _extract_factor_id(password_result, continue_url)
        secret = _account_totp_secret(email)
        if not factor_id:
            raise RuntimeError(f"密码登录后进入 MFA 但未拿到 factor_id: {password_result}")
        if not secret:
            raise RuntimeError(f"密码登录后进入 MFA，但账号没有 totp_secret：{email}")
        logger.info("[查活] 已进入 MFA challenge，开始提交 TOTP：%s factor_id=%s", email, factor_id)
        _mfa_issue_challenge(session, factor_id)
        code = _account_totp_code(email)
        if not code:
            raise RuntimeError(f"无法生成 TOTP 验证码：{email}")
        mfa_result = _mfa_verify(session, factor_id, code)
        mfa_continue_url = _extract_continue_url(mfa_result) or continue_url
        if not mfa_continue_url:
            raise RuntimeError(f"MFA 验证成功但没有 continue_url: {mfa_result}")
        return _follow_continue_and_fetch(
            session,
            mfa_continue_url,
            referer=f"https://auth.openai.com/mfa-challenge/{factor_id}",
        )

    if "email-verification" in continue_url or page_type in {"email_verification", "email_otp_send"}:
        logger.info("[查活] 密码登录后仍进入邮箱 OTP，继续完成邮箱验证：%s", email)
        return _login_via_email_otp(
            session,
            email,
            otp_after_ts,
            email_source=email_source,
        )

    if continue_url:
        logger.info("[查活] 密码登录直接给出回调地址，继续完成回调：%s", email)
        return _follow_continue_and_fetch(session, continue_url, referer="https://auth.openai.com/log-in/password")

    raise RuntimeError(f"密码登录成功但没有可用 continue_url: {password_result}")


def _login_via_full_web_flow(
    email: str,
    proxy: str | None,
    *,
    email_source: str | None,
    fingerprint_state: dict,
) -> tuple[BrowserSession, dict]:
    """按 plus 纯协议注册的 Web 登录序列建立一份全新登录态。"""
    session, authorize_url = _network_preflight_with_retry(
        email,
        proxy,
        fingerprint_state=fingerprint_state,
    )
    otp_after_ts = time.time()
    final_url = follow_authorize(session, authorize_url)
    dead_code = detect_account_unusable_text(final_url)
    if dead_code:
        raise AccountUnusableError(
            f"账号已废弃（{dead_code}）",
            error_code=dead_code,
        )
    session_info = _login_via_password_or_otp(
        session,
        email,
        otp_after_ts,
        email_source=email_source,
    )
    return session, session_info


def log_path(email: str) -> Path:
    safe = str(email or "").replace("/", "_").replace("\\", "_").replace(":", "_")
    return _LOG_DIR / f"live-check-{safe}.log"


def is_checking(email: str) -> bool:
    key = str(email or "").strip().lower()
    with _RUNNING_LOCK:
        return key in _RUNNING


def _validate_with_retry(
    session: BrowserSession,
    email: str,
    otp_after_ts: float,
    max_otp_attempts: int = 3,
    email_source: str | None = None,
) -> dict:
    current_otp = None
    last_exc: Exception | None = None
    for attempt in range(1, max_otp_attempts + 1):
        try:
            if current_otp is None:
                logger.info("[查活] 等待登录 OTP：%s（第 %s/%s 次）", email, attempt, max_otp_attempts)
                current_otp = wait_for_otp(
                    email,
                    after_ts=otp_after_ts,
                    email_source=email_source,
                )
            result = validate_email_otp(session, current_otp, sentinel_header=None, so_header=None)
            return result
        except EmailOtpInvalidError as exc:
            last_exc = exc
            if attempt >= max_otp_attempts:
                break
            logger.warning("[查活] OTP 无效/过期，重新发送后再取：%s", str(exc)[:180])
            send_email_otp(session)
            # 以“重新发送请求完成后”为新基准，避免刚刚失败的上一封旧码再次被 after 容忍窗口命中。
            otp_after_ts = time.time()
            current_otp = None
            time.sleep(1)
        except Exception as exc:
            if _is_otp_not_sent(exc):
                logger.warning("[查活] 登录阶段服务端未发送验证码（otp_not_sent），不再重试：%s", email)
                raise
            # 提交 OTP 后的网络抖动（连接断开/超时/代理波动）：同一会话重发验证码再验证一次。
            if attempt >= max_otp_attempts or not (
                _is_retryable_network_error(exc) or _is_otp_wait_timeout(exc)
            ):
                raise
            last_exc = exc
            logger.warning("[查活] OTP 验证网络抖动，重新发送后再取（%s/%s）：%s", attempt, max_otp_attempts, str(exc)[:180])
            try:
                send_email_otp(session)
            except Exception:
                raise
            otp_after_ts = time.time()
            current_otp = None
            time.sleep(1)
    raise last_exc if last_exc else RuntimeError("OTP 验证失败")


def check_account_liveness(
    email: str,
    proxy: str | None = None,
    *,
    clear_log: bool = True,
    email_source: str | None = None,
    fingerprint_state: dict | None = None,
) -> dict:
    """
    重新登录账号并刷新最新 accessToken。

    返回：
      {
        ok: bool,
        status: live/deactivated/failed,
        access_token: str?,
        session: dict?,
        checked_at: ISO,
        error: str?
      }
    """
    email = str(email or "").strip()
    if not email:
        raise ValueError("email 不能为空")

    checked_at = _now()
    key = email.lower()
    path = log_path(email)
    path.parent.mkdir(parents=True, exist_ok=True)
    if clear_log:
        path.write_text("", encoding="utf-8")

    fh: logging.FileHandler | None = None
    session: BrowserSession | None = None
    task_fingerprint_state = fingerprint_state if fingerprint_state is not None else {}
    root_logger = logging.getLogger()
    thread_name = threading.current_thread().name
    with _RUNNING_LOCK:
        _RUNNING.add(key)
    try:
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        fh.addFilter(lambda record: record.threadName == thread_name)
        root_logger.addHandler(fh)

        logger.info("[查活] 日志文件：%s", path)
        logger.info("[查活] 开始重新登录：%s", email)
        existing_access_token = _stored_access_token(email)
        fast_result = _probe_existing_access_token(email, existing_access_token)
        if fast_result is not None:
            return fast_result
        has_totp = bool(_account_totp_secret(email))
        # 注册时保存了密码的账号优先走"邮箱 → 密码 → MFA(TOTP)"，
        # 不再默认落回 reauth 的邮箱 OTP 链路：OTP 依赖邮件投递，
        # 服务端不发码时整条链路就废了，而密码本身是可靠的。
        password_available = bool(_account_registration_password(email))
        if password_available:
            logger.info(
                "[查活] 流程：登录页 → 密码 → MFA(TOTP，如有) → OAuth callback → Session/AT"
                "（账号已保存密码，跳过 reauth 邮件 OTP）"
            )
        if existing_access_token and not has_totp and not password_available:
            # 2FA 设置流程已经验证：先用已有 AT 预热 ChatGPT 登录态，再走
            # reauth → 邮箱 OTP → callback。该链路不依赖容易被 CF 拦截的
            # /api/auth/providers。已开启 TOTP 的账号保留密码 → MFA 路径，
            # 避免把 MFA challenge 误当成邮箱 OTP 页面。
            logger.info("[查活] 流程：登录态预热 → CSRF → Reauth Signin → Authorize → 邮箱 OTP → OAuth callback → Session/AT")
            session = _new_fingerprint_pinned_session(email, proxy, task_fingerprint_state)
            logger.info(
                "[查活] 指纹生命周期：%s",
                _fingerprint_mode_label(task_fingerprint_state),
            )
            logger.info(
                "[查活] 会话创建完成：proxy=%s device_id=%s（复用2FA稳定链路）",
                session.proxy or "直连/配置随机",
                session.device_id,
            )
            logger.info("[查活] 指纹摘要：%s", session.fingerprint_summary_text())
            _warm_authenticated_session(session, existing_access_token)
            human_delay("navigate")
            try:
                session_info = _login_via_reauth(
                    session,
                    email,
                    time.time(),
                    email_source=email_source,
                )
            except Exception as reauth_exc:
                if _is_otp_not_sent(reauth_exc):
                    logger.warning(
                        "[查活] 服务端未发送验证码（otp_not_sent），不再切换干净会话重试：%s", email,
                    )
                    raise
                # 取码超时（邮箱里没有新验证码）不能直接判失败：账号已停用时
                # 服务端根本不会发新验证码，继续等只会把真实状态藏起来。
                # 这类情况同样回退到完整登录，由服务端给出确定结论。
                if not (
                    _is_retryable_network_error(reauth_exc) or _is_otp_wait_timeout(reauth_exc)
                ):
                    raise
                # reauth/callback 的同会话阶段重试已经耗尽。继续复用熔断的
                # Cookie Jar 没有意义；参考 plus 纯协议注册，建立干净会话并按
                # /auth/login → providers/session/csrf/session → signin 完整重登。
                failed_proxy = session.proxy if proxy is None else proxy
                logger.warning(
                    "[查活] AT reauth 链临时失败，切换干净会话执行纯协议完整登录：%s",
                    str(reauth_exc)[:240],
                )
                try:
                    close_browser_session(session)
                except Exception:
                    pass
                session, session_info = _login_via_full_web_flow(
                    email,
                    failed_proxy,
                    email_source=email_source,
                    fingerprint_state=task_fingerprint_state,
                )
        else:
            # 兼容没有本地 AT 或已开启 TOTP 的记录，按 plus 成功注册样本复现
            # 登录页 document 与完整 NextAuth 调用顺序。
            logger.info(
                "[查活] 流程：登录页 → Providers/Session/CSRF/Session → Signin → "
                "Authorize → 密码/邮箱 OTP → MFA(如有) → OAuth callback → Session/AT"
            )
            session, session_info = _login_via_full_web_flow(
                email,
                proxy,
                email_source=email_source,
                fingerprint_state=task_fingerprint_state,
            )
        access_token = str(session_info.get("accessToken") or "")
        if not access_token:
            raise RuntimeError("重新登录后未拿到 accessToken")

        user = session_info.get("user") or {}
        account = session_info.get("account") or {}
        logger.info("[查活] 正常：%s user_id=%s plan=%s", email, user.get("id"), account.get("planType"))
        fp = _safe_fingerprint_for_account(session)
        return {
            "ok": True,
            "status": "live",
            "checked_at": checked_at,
            "access_token": access_token,
            "session": session_info,
            "proxy_used": session.proxy or None,
            "fingerprint": fp,
            "fingerprint_text": _safe_fingerprint_text_for_account(session),
        }
    except AccountUnusableError as exc:
        code = getattr(exc, "error_code", "") or detect_account_unusable_text(str(exc)) or "account_deactivated"
        logger.warning("[查活] 已废号：%s %s", email, code)
        return {"ok": False, "status": "deactivated", "checked_at": checked_at, "error": code}
    except Exception as exc:
        code = detect_account_unusable_text(_exception_response_text(exc)) or detect_account_unusable_text(str(exc))
        if code:
            logger.warning("[查活] 已废号：%s %s", email, code)
            return {"ok": False, "status": "deactivated", "checked_at": checked_at, "error": code}
        logger.warning("[查活] 失败：%s %s: %s", email, type(exc).__name__, str(exc)[:260])
        failure_status = "otp_not_sent" if _is_otp_not_sent(exc) else "failed"
        return {"ok": False, "status": failure_status, "checked_at": checked_at,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    finally:
        try:
            logger.info("[查活] 结束：%s", email)
            if session is not None:
                try:
                    close_browser_session(session)
                except Exception:
                    pass
            if fh is not None:
                root_logger.removeHandler(fh)
                fh.close()
        finally:
            with _RUNNING_LOCK:
                _RUNNING.discard(key)
