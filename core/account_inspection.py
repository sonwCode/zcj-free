# -*- coding: utf-8 -*-
"""账号本地巡检：先零网络判断 token 是否过期，再决定要不要真查活。

借鉴 myfanhua/turb-gpt-free-register 的 core/account_inspection_service.py。

它把"账号是否还活着"拆成两层：
  1. 本地读 JWT 的 exp（不发任何请求）—— 过期=必然失效，不用浪费代理和查活；
  2. 只有过期的号才入队查活。

我们此前的做法是对所有号直接查活：一个号要起会话、走代理、走 OAuth，
既慢又容易被风控判成"异常登录"。这一步能把绝大多数无意义查活省掉。
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: 提前多少秒判定"即将过期"，避免卡在边界上
DEFAULT_SKEW_SECONDS = 300


def _b64url_decode(segment: str) -> bytes:
    padding = '=' * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_payload(token: str) -> dict:
    """只解码 payload，不验签；失败返回空 dict。"""
    parts = str(token or '').split('.')
    if len(parts) != 3 or not all(parts):
        return {}
    try:
        data = json.loads(_b64url_decode(parts[1]).decode('utf-8', 'ignore'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def token_state(token: str, *, now: float | None = None, skew: int = DEFAULT_SKEW_SECONDS) -> dict:
    """返回 {state, exp, remaining}；state ∈ valid/expiring/expired/invalid。"""
    payload = decode_jwt_payload(token)
    exp = payload.get('exp')
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return {'state': 'invalid', 'exp': None, 'remaining': None}
    current = time.time() if now is None else now
    remaining = float(exp) - current
    if remaining <= 0:
        state = 'expired'
    elif remaining <= max(0, int(skew)):
        state = 'expiring'
    else:
        state = 'valid'
    return {'state': state, 'exp': int(exp), 'remaining': int(remaining)}


def needs_refresh(token: str, *, now: float | None = None, skew: int = DEFAULT_SKEW_SECONDS) -> bool:
    return token_state(token, now=now, skew=skew)['state'] != 'valid'


@dataclass
class InspectionReport:
    total: int = 0
    valid: int = 0
    expiring: int = 0
    expired: int = 0
    invalid: int = 0
    deactivated_skipped: int = 0
    need_refresh_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'total': self.total,
            'valid': self.valid,
            'expiring': self.expiring,
            'expired': self.expired,
            'invalid': self.invalid,
            'deactivated_skipped': self.deactivated_skipped,
            'need_refresh': len(self.need_refresh_ids),
            'need_refresh_ids': self.need_refresh_ids[:200],
        }


def inspect_local(*, account_ids: list[int] | None = None, skew: int = DEFAULT_SKEW_SECONDS,
                  include_archived: bool = False) -> InspectionReport:
    """零网络巡检：只读库里的 access_token，判断是否需要刷新。"""
    from core import db

    rows = db.list_accounts(limit=100000) or []
    wanted = {int(x) for x in (account_ids or []) if str(x).strip()}
    report = InspectionReport()
    for acc in rows:
        try:
            acc_id = int(acc.get('id') or 0)
        except Exception:
            continue
        if wanted and acc_id not in wanted:
            continue
        if not include_archived and acc.get('archived'):
            continue
        report.total += 1
        if str(acc.get('live_check_status') or '').strip().lower() == 'deactivated':
            report.deactivated_skipped += 1
            continue
        state = token_state(acc.get('access_token') or '', skew=skew)
        key = state['state']
        if key == 'valid':
            report.valid += 1
        elif key == 'expiring':
            report.expiring += 1
            report.need_refresh_ids.append(acc_id)
        elif key == 'expired':
            report.expired += 1
            report.need_refresh_ids.append(acc_id)
        else:
            report.invalid += 1
            report.need_refresh_ids.append(acc_id)
    return report


# ---------------------------------------------------------------- 网络巡检

ME_URL = 'https://chatgpt.com/backend-api/me'


def _device_id_for(email: str) -> str:
    """按邮箱派生稳定设备 id（同一账号每次一致，避免每次换 id 反成异常特征）。"""
    import uuid

    key = str(email or '').strip().lower() or 'anonymous'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'chatgpt-device:' + key))


def _probe_me(token: str, proxy: str | None, timeout: float = 20.0,
              device_id: str | None = None) -> tuple[str, str]:
    """单次 /me 探测：返回 (结论, 细节)。结论 ∈ alive/revoked/unknown。"""
    import requests

    # 请求头走统一生成器：补 Origin / Referer / ChatGPT-Account-ID / OAI-Device-Id。
    # dango 实测：带 Authorization 却不带 Origin/Referer 是典型非浏览器特征。
    try:
        from core.auth_headers import account_api_headers

        headers = account_api_headers(token, device_id=str(device_id or ''))
    except Exception:
        headers = {'Authorization': 'Bearer ' + str(token or ''), 'Accept': 'application/json'}
    headers.setdefault('User-Agent',
                       'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
    proxies = {'http': proxy, 'https': proxy} if proxy else None
    try:
        # /me 是幂等 GET：TLS/连接类瞬时错误值得重试一次，
        # 否则一次网络抖动就会把好号记成 unknown/revoked。
        from core.transient_retry import retry_transient

        resp = retry_transient(
            lambda: requests.get(ME_URL, headers=headers, proxies=proxies,
                                 timeout=timeout, allow_redirects=False),
            attempts=2, base_delay=1.0, tag='/me 探活',
        )
    except Exception as exc:
        return 'unknown', '%s: %s' % (type(exc).__name__, str(exc)[:100])
    code = int(getattr(resp, 'status_code', 0) or 0)
    body = (resp.text or '')[:300].lower()
    if code == 200:
        return 'alive', '200'
    # 分类要点：
    #   401 / token_revoked        → 凭据失效（可重登恢复），记 revoked
    #   任何状态码 + 结构化封禁标记 → 真封禁，记 revoked 并注明 ban
    #   403 但没有封禁标记         → 多半是 IP/CF 拦截，记 unknown（别误判成号废）
    try:
        from core.ban_detect import classify_token_failure

        verdict = classify_token_failure(code, body)
    except Exception:
        verdict = {'kind': 'unknown', 'reason': '', 'ban_code': ''}
    if verdict.get('kind') == 'banned':
        return 'revoked', 'ban:%s' % (verdict.get('ban_code') or verdict.get('reason') or 'banned')
    ban = verdict.get('ban_code') or ''
    if ban:
        return 'revoked', 'ban:%s %s' % (ban, body[:60])
    if code == 401 or 'token_revoked' in body:
        return 'revoked', '%s %s' % (code, body[:80])
    if code == 403:
        return 'unknown', 'http-403(no-ban-marker)'
    return 'unknown', 'http-%s' % code


def inspect_network(*, account_ids: list[int] | None = None, workers: int = 4,
                    timeout: float = 20.0, update_db: bool = True,
                    include_archived: bool = False) -> dict:
    """一次 /me 探活（不走 OAuth，不起浏览器），并可选把结论写回库。"""
    from concurrent.futures import ThreadPoolExecutor

    from config.proxy import PROXY_POOL, pick_proxy_excluding
    from core import db

    rows = db.list_accounts(limit=100000) or []
    wanted = {int(x) for x in (account_ids or []) if str(x).strip()}
    targets = []
    for acc in rows:
        acc_id = int(acc.get('id') or 0)
        if wanted and acc_id not in wanted:
            continue
        if not include_archived and acc.get('archived'):
            continue
        token = str(acc.get('access_token') or '')
        if not token:
            continue
        targets.append((acc_id, str(acc.get('email') or ''), token))

    results: dict[str, list] = {'alive': [], 'revoked': [], 'unknown': []}

    def _work(item):
        acc_id, email, token = item
        proxy = pick_proxy_excluding() if PROXY_POOL else None
        verdict, detail = _probe_me(token, proxy, timeout=timeout, device_id=_device_id_for(email))
        return acc_id, email, verdict, detail

    with ThreadPoolExecutor(max_workers=max(1, int(workers or 1))) as pool:
        for acc_id, email, verdict, detail in pool.map(_work, targets):
            results[verdict].append({'id': acc_id, 'email': email, 'detail': detail})
            if update_db:
                try:
                    if verdict == 'alive':
                        db.update_account_liveness(acc_id, {
                            'ok': True, 'status': 'live',
                            'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                        })
                    elif verdict == 'revoked':
                        db.update_account_liveness(acc_id, {
                            'ok': False, 'status': 'deactivated',
                            'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                            'error': 'token_revoked（一次 /me 探活）',
                        })
                except Exception as exc:
                    logger.debug('[巡检] 写回状态失败 #%s：%s', acc_id, str(exc)[:80])
    summary = {k: len(v) for k, v in results.items()}
    summary['total'] = len(targets)
    logger.info('[巡检] 网络探活完成：%s', summary)
    return {'summary': summary, 'detail': results}
