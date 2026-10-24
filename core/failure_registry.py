# -*- coding: utf-8 -*-
"""失败词汇单一注册表（借鉴 2951461586 的 kernel/failure_registry.py）。

为什么需要：
  我们此前只有 success/failed 两种结果 + 一段 error 字符串。
  结果就是：没人能回答"这批失败里有多少是出口问题、多少是邮箱问题、
  多少是账号本身已废"，每次排查都靠人工 grep 日志。

设计要点（照搬它的思路）：
  * FAILURE_CLASSES 的顺序就是匹配优先序（越靠前越先命中）；
  * 四个语义轴彼此独立，不能合并：
      attempt_retryable       同账号可以立刻重试
      retain_for_future_batch 可以留到下一批再试
      batch_dropped           该号应从批次里掉号
      rotate_egress           换一条出口是合理补救（不等于一定是出口问题）
  * 标记字符串是线上数据（日志/库里已经存在的文本），逐字保留，不做顺手规范化。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureClass:
    code: str
    markers: tuple[str, ...]
    attempt_retryable: bool = False
    retain_for_future_batch: bool = False
    batch_dropped: bool = False
    rotate_egress: bool = False


FAILURE_CLASSES: tuple[FailureClass, ...] = (
    FailureClass(
        'cancelled',
        (
            'registration_cancelled',
            'cancelled_by_user',
            'stop_requested',
            '已被用户取消',
        ),
    ),
    FailureClass(
        'internal',
        (
            'nameerror',
            'attributeerror',
            'typeerror',
            'keyerror',
            'importerror',
            'indexerror',
            'unboundlocalerror',
            'syntaxerror',
        ),
        batch_dropped=True,
    ),
    FailureClass(
        'configuration',
        (
            'unsupported_registration_driver',
            'missing_dependency',
            'invalid_configuration',
            '未安装 cloakbrowser',
        ),
        batch_dropped=True,
    ),
    FailureClass(
        'account',
        (
            'account_deactivated',
            'warning_banner',
            'account has been deactivated',
            'token_revoked',
            'account has been deleted',
            '账号已废',
        ),
        batch_dropped=True,
    ),
    FailureClass(
        'phone_required',
        ('手机号验证重试', '手机验证重试', 'phone number required',
         'add-phone', 'phone_verification_required', 'whatsapp_channel_reverted',
         '没有新的价格范围内国家', '无可用号码', 'phone_numbers_exhausted'),
        # 需要绑手机才能继续：这是外部依赖（号码质量/风控），不是代码问题。
        # 同一个号过一段时间可能又不需要，所以保留到下一批再试，而不是直接掉号。
        retain_for_future_batch=True,
    ),
    FailureClass(
        'mailbox',
        (
            'mailbox_auth_invalid',
            'mailbox_endpoint_unavailable',
            'remailerror',
            '收件箱为空',
            '等待 remail 验证码超时',
            '验证码超时',
            'otp timeout',
            '账号上下文',
        ),
        retain_for_future_batch=True,
    ),
    FailureClass(
        'rate_limit',
        (
            'rate_limit_exceeded',
            'registration_rate_limited',
            'registration_rate_limit_circuit_open',
            'too many requests',
            'http-429',
            'status=429',
        ),
        attempt_retryable=True,
        retain_for_future_batch=True,
        rotate_egress=True,
    ),
    FailureClass(
        'edge_challenge',
        (
            'cloudflare challenge',
            'cloudflarechallengeerror',
            'cf-chl',
            'just a moment',
            'attention required',
        ),
        attempt_retryable=True,
        retain_for_future_batch=True,
        rotate_egress=True,
    ),
    FailureClass(
        'network',
        (
            'sslerror',
            'ssl_error_syscall',
            'connection reset',
            'connection aborted',
            'connection refused',
            'curl: (35)',
            'curl: (56)',
            'read timed out',
            'proxy',
            '远程主机强迫关闭',
        ),
        attempt_retryable=True,
        retain_for_future_batch=True,
        rotate_egress=True,
    ),
    FailureClass(
        'auth_state',
        (
            'invalid_auth_step',
            'invalid_state',
            'email_submit_stalled',
            'missing_continue_with_password',
            '未进入密码页',
            'password_required_but_missing',
            '找不到邮箱输入框',
            '找不到 otp 输入框',
            'accesstoken 超时',
        ),
        attempt_retryable=True,
        retain_for_future_batch=True,
    ),
)

#: 硬停标记：出现即整批停下（不重试、不留批）
TERMINAL_ERROR_MARKERS: tuple[str, ...] = (
    'registration_cancelled',
    'cancelled_by_user',
    'missing_dependency',
    'unsupported_registration_driver',
)


def error_text(value) -> str:
    """把任意失败值转成小写文本（dict 取 error/message/reason/detail/status）。"""
    if isinstance(value, dict):
        parts = [str(value.get(k) or '') for k in ('error', 'message', 'reason', 'detail', 'status')]
        return ' '.join(parts).lower()
    return str(value or '').lower()


def classify_error(value) -> str:
    """按优先序返回失败类别 code；无匹配返回 unknown。"""
    text = error_text(value)
    if not text.strip():
        return 'unknown'
    for item in FAILURE_CLASSES:
        for marker in item.markers:
            if marker in text:
                return item.code
    return 'unknown'


def class_of(code: str):
    for item in FAILURE_CLASSES:
        if item.code == code:
            return item
    return None


def is_terminal_registration_error(value) -> bool:
    text = error_text(value)
    return any(marker in text for marker in TERMINAL_ERROR_MARKERS)


def rotate_egress_suggested(value) -> bool:
    """这次失败是否换出口值得一试。"""
    item = class_of(classify_error(value))
    return bool(item and item.rotate_egress)


def summarize(results: list) -> dict:
    """把一批结果按类别汇总，用于批次报表。"""
    counts: dict[str, int] = {}
    for entry in results or []:
        if isinstance(entry, dict) and entry.get('success'):
            code = 'success'
        else:
            code = classify_error(entry)
        counts[code] = counts.get(code, 0) + 1
    return counts
