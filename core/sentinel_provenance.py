# -*- coding: utf-8 -*-
"""Sentinel 签名层的溯源与漂移检测。

借鉴 absoultly-classical/GPT-Register-Tool 的 sentinel/bundle.py：
它对 sdk.js / sentinel-runner.js 做 SHA256 固定，并把 SDK 版本作为常量管理。

为什么需要：签名 SDK 一旦被 OpenAI 换版本，旧的 so/token 结构就失效，
注册会**静默**变成低信任账号——没有任何报错，只是号活不久。
锁哈希 + 版本探测能把"SDK 被换掉"从沉默失败变成可观测事件。

本模块只做三件事（全部只读）：
  1. 计算本地 sdk.js / sentinel-runner.js 的 sha256 与大小；
  2. 用当前 SENTINEL_SV 去拉线上同名 sdk.js，比较哈希 → ok / drift / retired；
  3. 生成一份 provenance（版本+哈希+时间），写进账号 extra 便于事后归因。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SENTINEL_DIR = Path('/opt/turb-gpt-register/sentinel')
SDK_PATH = SENTINEL_DIR / 'sdk.js'
RUNNER_PATH = SENTINEL_DIR / 'sentinel-runner.js'
LIVE_SDK_URL = 'https://sentinel.openai.com/sentinel/{sv}/sdk.js'


def _digest(path: Path) -> dict:
    if not path.is_file():
        return {'path': str(path), 'exists': False, 'sha256': '', 'bytes': 0}
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return {'path': str(path), 'exists': True, 'sha256': value.hexdigest(),
            'bytes': path.stat().st_size}


def local_fingerprint() -> dict:
    """本地签名运行时指纹。"""
    from config.openai_protocol import SENTINEL_SV

    return {'sv': str(SENTINEL_SV), 'sdk': _digest(SDK_PATH), 'runner': _digest(RUNNER_PATH)}


def check_drift(*, timeout: float = 20.0) -> dict:
    """用当前 sv 拉线上 sdk.js 比对本地；返回 ok / drift / retired / error。"""
    local = local_fingerprint()
    sv = local['sv']
    result = {'sv': sv, 'status': 'error', 'detail': '', 'live': {}, 'local_sdk_sha256': local['sdk']['sha256']}
    if not local['sdk']['exists']:
        result.update(status='error', detail='本地 sdk.js 缺失')
        return result
    try:
        import requests

        resp = requests.get(LIVE_SDK_URL.format(sv=sv), timeout=timeout,
                            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'})
    except Exception as exc:
        result.update(status='error', detail='%s: %s' % (type(exc).__name__, str(exc)[:120]))
        return result
    code = int(getattr(resp, 'status_code', 0) or 0)
    body = resp.content or b''
    live_sha = hashlib.sha256(body).hexdigest() if body else ''
    result['live'] = {'http': code, 'bytes': len(body), 'sha256': live_sha}
    if code == 404:
        result.update(status='retired', detail='当前 sv 在线上已不存在，SDK 版本过期')
    elif code == 200 and live_sha == local['sdk']['sha256']:
        result.update(status='ok', detail='本地与线上 SDK 完全一致')
    elif code == 200 and len(body) > 5000:
        result.update(status='drift', detail='线上同名地址内容已变化（%d vs %d 字节）'
                                               % (len(body), local['sdk']['bytes']))
    else:
        result.update(status='error', detail='线上返回 %s / %d 字节' % (code, len(body)))
    return result


def provenance(*, include_drift: bool = False) -> dict:
    """生成可落库的 provenance 快照。"""
    data = local_fingerprint()
    data['recorded_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    if include_drift:
        data['drift'] = check_drift()
    return data


_LAST_CHECK: dict = {'at': 0.0, 'result': None}
_CHECK_TTL = 1800.0


def _check_and_log() -> dict:
    result = check_drift()
    if result['status'] == 'ok':
        logger.info('[签名层] SDK 版本 %s 与线上一致', result['sv'])
    elif result['status'] == 'retired':
        logger.error('[签名层] SDK 版本 %s 已下线，需更新 SENTINEL_SV 与 sentinel/sdk.js', result['sv'])
    elif result['status'] == 'drift':
        logger.warning('[签名层] SDK 内容漂移：%s', result['detail'])
    else:
        logger.debug('[签名层] 漂移检测跳过：%s', result['detail'])
    return result


def log_drift_if_any(ttl: float = _CHECK_TTL) -> dict:
    """带缓存的漂移告警：同一进程内默认 30 分钟只查一次。"""
    import time as _time

    now = _time.time()
    cached = _LAST_CHECK.get('result')
    if cached is not None and now - float(_LAST_CHECK.get('at') or 0) < ttl:
        return cached
    result = _check_and_log()
    _LAST_CHECK.update(at=now, result=result)
    return result
