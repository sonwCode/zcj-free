# -*- coding: utf-8 -*-
"""协议注册（无浏览器）：用 curl_cffi 会话走完 signup，流量从 ~21MB 降到几百 KB。

流程（与浏览器页面同序）：
  1. 网络预检（出口 IP / CF Cookie）
  2. 打开 /auth/login 建立匿名 document
  3. password sentinel bundle（email_otp_validate / username_password_create / authorize_continue）
  4. POST /api/accounts/user/register 提交邮箱+密码
  5. 跟随 continue_url 触发邮件 OTP
  6. 轮询邮箱拿 6 位码 → POST email-otp/validate
  7. 打开 about-you → POST create_account 填姓名生日
  8. GET /api/auth/session 取 accessToken，落库

对外只暴露 run_protocol_registration()。
"""
from __future__ import annotations

import json
import logging
import time

from core import db
from core import openai_auth as oa
from core.session import BrowserSession, close_browser_session

logger = logging.getLogger(__name__)


def _log_phase(name: str, ok: bool, detail: str = "") -> None:
    logger.info("[协议注册][%s] %s%s", name, "OK" if ok else "失败", ("：" + detail) if detail else "")


def run_protocol_registration(
    email: str,
    name: str,
    birthday: str,
    proxy: str | None = None,
    job_id: int | None = None,
    otp_max_wait: int = 180,
) -> dict:
    """返回值与 Cloak 注册保持一致的关键字段：account_id / error / network_traffic。"""
    if not str(email or "").strip():
        # 调用方可能传空（沿用 Cloak 那条线的"内部取邮箱"语义）
        from core.email_provider import acquire_email

        email = acquire_email()
    email = str(email).strip()

    session = None
    started_at = time.time()
    traffic = {
        "available": False,
        "method": "protocol.curl_cffi",
        "label": "Protocol",
        "note": "协议注册不加载任何静态资源，仅接口请求",
    }
    try:
        session = BrowserSession(proxy=proxy)
        oa.network_preflight(session)
        _log_phase("预检", True)

        # 先建立 ChatGPT 侧登录事务：CSRF → signin/openai 拿 authorize URL。
        # 少这一步，后面的 authorize/continue 会返回 409 invalid_state。
        from core import chatgpt_auth

        session.get(
            "https://chatgpt.com/auth/login",
            headers=session.get_chatgpt_headers(referer="https://chatgpt.com/"),
            allow_redirects=True,
        )
        csrf = chatgpt_auth.get_csrf_token(session)
        authorize_url = chatgpt_auth.signin_openai(session, csrf, email)
        _log_phase("建立登录事务", True, str(authorize_url)[:70])
        # 必须真的跟一次授权页，auth.openai.com 才会落下登录会话（否则 409 invalid_state）
        try:
            final_url = oa.follow_authorize(session, authorize_url)
            _log_phase("跟随授权页", True, str(final_url)[:70])
        except Exception as exc:
            _log_phase("跟随授权页", False, "%s: %s" % (type(exc).__name__, str(exc)[:120]))

        # 第 1 步：先建立授权事务（signup screen_hint），否则后续 user/register 直接 400。
        # 参照 aBaiFreeGPT/platforms/chatgpt/protocol_register.py 的顺序：
        #   authorize/continue (flow=authorize_continue) → user/register (flow=username_password_create)
        cont_sentinel = oa.request_sentinel_token(session, "authorize_continue")
        cont_header, cont_so = oa.build_sentinel_header(session, cont_sentinel, "authorize_continue")
        cont_headers = session.get_auth_headers(referer="https://auth.openai.com/create-account")
        cont_headers["openai-sentinel-token"] = cont_header
        if cont_so:
            cont_headers["openai-sentinel-so-token"] = cont_so
        cont = session.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers=cont_headers,
            data=json.dumps(
                {"username": {"value": email, "kind": "email"}, "screen_hint": "signup"},
                separators=(",", ":"),
            ),
        )
        if cont.status_code >= 400:
            raise RuntimeError("authorize/continue 失败 status=%s body=%s" % (
                cont.status_code, (cont.text or "")[:200]))
        cont_data = cont.json() if cont.text else {}
        _log_phase("authorize/continue", True, str(cont_data.get("page", {}).get("type") or "")[:40])

        # 第 2 步：设置密码（password sentinel 用同一 iframe bundle）
        bundle = oa.request_password_sentinel_bundle(session)
        sentinel_header, so_header = oa.build_sentinel_header(
            session, bundle, "username_password_create"
        )
        if not sentinel_header:
            raise RuntimeError("未能生成 username_password_create sentinel")
        _log_phase("sentinel", True)

        password = oa.generate_registration_password()
        reg = oa.register_user(session, email, password, sentinel_header, so_header)
        _log_phase("提交邮箱密码", True, "continue_url=%s" % str(reg.get("continue_url") or "")[:60])

        continue_url = str(reg.get("continue_url") or "") or None
        oa.navigate_email_otp_send(session, continue_url)
        _log_phase("触发邮件 OTP", True)

        from core.email_provider import wait_for_otp

        code = wait_for_otp(email, after_ts=started_at, max_wait=otp_max_wait)
        if not code:
            raise RuntimeError("未取到邮箱验证码")
        _log_phase("取码", True, str(code))

        otp_sentinel = oa.request_sentinel_token(session, "email_otp_validate")
        otp_header, otp_so = oa.build_sentinel_header(session, otp_sentinel, "email_otp_validate")
        oa.validate_email_otp(session, code, otp_header, otp_so)
        _log_phase("校验邮箱码", True)

        oa.navigate_about_you(session)

        # registration_disallowed 是策略级拒绝：同一个 sentinel 几乎不会变通，
        # 但刷新 sentinel（新 turnstile/poW）后有相当概率通过，重试最多 3 次。
        # 参考 hyhang915/gptfree-register 的处理方式。
        created = None
        last_exc = None
        for attempt in range(1, 4):
            about_sentinel = oa.request_sentinel_token(session, "oauth_create_account")
            about_header, about_so = oa.build_sentinel_header(
                session, about_sentinel, "oauth_create_account"
            )
            try:
                created = oa.create_account(session, name, birthday, about_header, about_so)
                _log_phase("创建账号", True, str(created)[:80])
                break
            except Exception as exc:
                last_exc = exc
                text = str(exc).lower()
                if "registration_disallowed" not in text or attempt >= 3:
                    raise
                wait_s = 2.0 * attempt
                logger.warning(
                    "[协议注册] registration_disallowed，刷新 sentinel 后重试 %s/3（%.0fs）",
                    attempt + 1, wait_s,
                )
                time.sleep(wait_s)
                try:
                    oa.navigate_about_you(session)
                except Exception:
                    pass
        if created is None and last_exc is not None:
            raise last_exc

        # 完成 OAuth callback（continue_url 指向 chatgpt.com/api/auth/callback/openai?code=...），
        # 不跟这一步就拿不到 accessToken。
        cb = str((created or {}).get("continue_url") or "")
        if cb:
            try:
                resp = session.get(
                    cb,
                    headers=session.get_chatgpt_headers(referer="https://auth.openai.com/about-you"),
                    allow_redirects=True,
                )
                _log_phase("完成 callback", True, "status=%s" % getattr(resp, "status_code", "-"))
            except Exception as exc:
                _log_phase("完成 callback", False, "%s: %s" % (type(exc).__name__, str(exc)[:120]))

        from core.account_export import fetch_session

        info = fetch_session(session) or {}
        access_token = str(info.get("accessToken") or info.get("access_token") or "")
        user = info.get("user") or {}
        if not access_token:
            raise RuntimeError("create_account 成功但未取到 accessToken")

        account_id = db.insert_account(
            email=email,
            access_token=access_token,
            user_id=str(user.get("id") or ""),
            user_name=str(user.get("name") or name),
            plan_type="free",
            device_id=str(getattr(session, "device_id", "") or ""),
            proxy_used=str(getattr(session, "proxy", "") or ""),
            extra={
                "registration_password": password,
                "registration_driver": "protocol",
                "session": info,
            },
            codex_status="skipped",
        )
        _log_phase("落库", True, "account_id=%s" % account_id)

        # 与浏览器路径一致：注册成功后自动排队查套餐/资格
        try:
            from core import plan_check_service

            plan_check_service.enqueue_account_plan_check(
                account_id=int(account_id),
                email=email,
                access_token=access_token,
                trigger="registration_auto",
                proxy=str(getattr(session, "proxy", "") or "") or None,
            )
            _log_phase("套餐检查入队", True)
        except Exception as exc:
            _log_phase("套餐检查入队", False, str(exc)[:100])

        traffic["duration_seconds"] = round(time.time() - started_at, 1)
        return {
            "success": True,
            "task_status": "success",
            "account_status": "success",
            "account_id": account_id,
            "email": email,
            "codex_status": "skipped",
            "registration_password": password,
            "network_traffic": traffic,
            "error": None,
        }
    except Exception as exc:
        logger.warning("[协议注册] 失败：%s: %s", type(exc).__name__, str(exc)[:300])
        traffic["duration_seconds"] = round(time.time() - started_at, 1)
        return {
            "success": False,
            "task_status": "failed",
            "account_id": None,
            "email": email,
            "network_traffic": traffic,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
        }
    finally:
        if session is not None:
            try:
                close_browser_session(session)
            except Exception:
                pass
