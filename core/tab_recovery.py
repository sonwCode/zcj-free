# -*- coding: utf-8 -*-
"""浏览器标签崩溃/断连的识别与一次重开。

来源：pirder/codex_me 的 `codex_keep_alive`：
`_is_page_disconnected_error` + `_recover_browser_tab` + `_navigate_with_tab_recovery`。

它的做法：导航前先做一次 `tab.url` 健康检查；
抛 `PageDisconnectedError`（或中文"与页面的连接已断开"）就整体重开浏览器/标签，
然后重试导航，最多 N 次。

我们目前**没有这类识别**：cloak 驱动的标签一旦崩，整轮注册直接判失败。
本模块先提供"识别 + 有界重开"，不强行改现有驱动。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 断连类关键字（小写匹配）
DISCONNECT_MARKERS = (
    'pagedisconnectederror',
    'page disconnected',
    '与页面的连接已断开',
    'target closed',
    'session closed',
    'browser has been closed',
    'no such window',
    'invalid session id',
)


def is_page_disconnected_error(error) -> bool:
    """判断异常是否属于"页面/浏览器连接已断开"。"""
    if error is None:
        return False
    name = type(error).__name__.lower()
    if 'disconnected' in name:
        return True
    text = str(error or '').lower()
    return any(marker in text for marker in DISCONNECT_MARKERS)


class TabRecoveryFailed(RuntimeError):
    """重开标签后仍然失败。"""


def run_with_tab_recovery(open_session, action, *,
                          close_session=None, attempts: int = 2,
                          tag: str = 'tab'):
    """执行 action(session)，遇断连则重开一次再试。

    open_session() -> session
    close_session(session) -> None（可选）
    action(session) -> Any
    """
    attempts = max(1, int(attempts or 1))
    last = None
    session = None
    for i in range(1, attempts + 1):
        try:
            if session is None:
                session = open_session()
            return action(session)
        except Exception as exc:
            last = exc
            if not is_page_disconnected_error(exc):
                raise                      # 业务错误原样抛出，不重开
            if i >= attempts:
                raise TabRecoveryFailed('%s 重试 %d 次仍失败：%s: %s'
                                        % (tag, attempts, type(exc).__name__, str(exc)[:120])) from exc
            logger.warning('[%s] 第 %d/%d 次遇到断连（%s），重开会话后重试',
                           tag, i, attempts, type(exc).__name__)
            if close_session is not None and session is not None:
                try:
                    close_session(session)
                except Exception:
                    pass
            session = None
    raise TabRecoveryFailed('%s 重试 %d 次仍失败：%s' % (tag, attempts, last))
