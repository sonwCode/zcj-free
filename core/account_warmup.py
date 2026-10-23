# -*- coding: utf-8 -*-
"""注册后"暖号"：模拟真实网页端启动，让账号留下正常使用痕迹。

背景：协议注册的号存活率明显低于浏览器注册。差别在于浏览器线会自然加载
chatgpt.com 前端、打满 bootstrap 接口，留下真实的会话/设备痕迹；而纯协议
建号后只有一次 OAuth 回调，属于"建完就没人用过"的账号，容易被清理。

这里按参考仓库（Haohuangsix / myfanhua）的做法，在建号后用同一个 device
指纹补一轮完整 bootstrap + conversation/init + 会话列表，成本约 1~2 MB。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_API_BASE = "https://chatgpt.com/backend-api"


def _safe(label: str, fn):
    try:
        return fn()
    except Exception as exc:  # 暖号失败不能影响注册结果
        logger.debug("[暖号] %s 失败：%s", label, str(exc)[:160])
        return None


def warmup_account(session, access_token: str, *, timezone_offset_min: str = "-") -> dict:
    """用注册时的会话/指纹打一轮真实前端请求，返回各步结果摘要。"""
    token = str(access_token or "").strip()
    if not token:
        return {"ok": False, "error": "缺少 access_token"}
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token

    referer = "https://chatgpt.com/"
    steps: dict[str, object] = {}

    def h():
        headers = session.get_chatgpt_headers(referer=referer)
        headers["authorization"] = token
        return headers

    # 1) 完整 bootstrap（light=False）：models / conversation-init / conversations 等
    from core.chatgpt_bootstrap import authenticated_bootstrap

    try:
        authenticated_bootstrap(session, access_token, light=False)
        steps["bootstrap"] = "ok"
    except Exception as exc:
        steps["bootstrap"] = str(exc)[:120]

    # 2) 会话初始化（网页端每次新开对话都会打）
    steps["conversation_init"] = _safe(
        "conversation/init",
        lambda: session.post(
            f"{_API_BASE}/conversation/init",
            json={
                "requested_default_model": None,
                "conversation_id": None,
                "timezone_offset_min": timezone_offset_min,
                "conversation_origin": None,
            },
            headers=h(),
            referer=referer,
        ).status_code,
    )

    # 3) 会话列表（真实用户进入侧边栏会拉取）
    steps["conversations"] = _safe(
        "conversations 列表",
        lambda: session.get(
            f"{_API_BASE}/conversations?offset=0&limit=28&order=updated",
            headers=h(),
            referer=referer,
        ).status_code,
    )

    # 4) 账号信息（确认新 token 可用）
    steps["me"] = _safe(
        "me",
        lambda: session.get(
            f"{_API_BASE}/me",
            headers=h(),
            referer=referer,
        ).status_code,
    )

    ok = steps.get("me") == 200
    logger.info("[暖号] 完成：me=%s conversations=%s init=%s bootstrap=%s",
                steps.get("me"), steps.get("conversations"),
                steps.get("conversation_init"), steps.get("bootstrap"))
    return {"ok": ok, "steps": steps}
