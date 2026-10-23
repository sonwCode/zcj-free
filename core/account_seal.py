# -*- coding: utf-8 -*-
"""账号浏览器落地（seal）。

协议线建号只要 30 秒，但账号在 10 分钟内就被 OpenAI 吊销，因为整个注册过程
没有任何真实浏览器行为。参考 GuJumpgate：每个号都活在真实指纹浏览器 profile
里（AdsPower），并用同一 profile 读取 /api/auth/session。

这里做同样的事：
1. 取协议注册会话里的 Cookie（curl_cffi 存在 cookies.jar）；
2. 用该账号专属 profile（按邮箱命名、持久化到磁盘）启动 CloakBrowser；
3. 注入 Cookie，打开 chatgpt.com，停留 dwell 秒让前端正常打接口；
4. 用页面内 fetch 复核是否真的已登录；
5. 关闭浏览器 —— profile 留在磁盘，之后所有操作复用同一身份。
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

_SAFE = re.compile(r'[^A-Za-z0-9_.-]+')

_SESSION_PROBE_JS = '''
const done = arguments[arguments.length - 1];
fetch('/api/auth/session', {credentials: 'include'})
  .then(r => r.json())
  .then(j => done(!!(j && j.accessToken)))
  .catch(() => done(false));
'''


def profile_name_for(email: str) -> str:
    """按邮箱生成稳定的 profile 名，保证同一账号永远用同一个目录。"""
    safe = _SAFE.sub('-', str(email or '').strip().lower()).strip('.-_')
    return ('acct-' + (safe or 'unknown'))[:64]


def _cookies_from_session(session) -> list[dict]:
    """把协议会话里的 Cookie 转成 Selenium add_cookie 需要的结构。"""
    out: list[dict] = []
    curl_session = getattr(session, 'session', None)
    jar = getattr(getattr(curl_session, 'cookies', None), 'jar', None)
    if jar is None:
        return out
    for c in list(jar):
        try:
            name = str(getattr(c, 'name', '') or '')
            value = getattr(c, 'value', None)
            domain = str(getattr(c, 'domain', '') or '')
            path = str(getattr(c, 'path', '/') or '/')
        except Exception:
            continue
        if not name or value is None:
            continue
        cookie = {'name': name, 'value': str(value), 'path': path}
        if domain:
            cookie['domain'] = domain if domain.startswith('.') else '.' + domain.lstrip('.')
        out.append(cookie)
    return out


def seal_account(email: str, session, *, dwell_seconds: int = 45,
                 proxy: str | None = None) -> dict:
    """用真实指纹浏览器给刚注册的账号"落地"，返回执行摘要。"""
    profile_name = profile_name_for(email)
    cookies = _cookies_from_session(session)
    started = time.time()
    driver = None
    try:
        from core.cloakbrowser_driver import build_cloak_driver, close_cloak_driver
    except Exception as exc:
        return {'ok': False, 'error': 'cloak 驱动不可用：%s' % str(exc)[:120]}

    try:
        driver, _info = build_cloak_driver(proxy=proxy, profile_name=profile_name)
        logger.info('[落地] 浏览器已启动 profile=%s cookies=%d', profile_name, len(cookies))
        driver.get('https://chatgpt.com/')
        # 自定义 CloakSeleniumDriver 没有 add_cookie，用 CDP Network.setCookies 注入
        added = 0
        try:
            payload = []
            for cookie in cookies:
                item = {'name': cookie['name'], 'value': cookie['value'], 'path': cookie.get('path') or '/'}
                if cookie.get('domain'):
                    item['domain'] = cookie['domain']
                else:
                    item['url'] = 'https://chatgpt.com/'
                payload.append(item)
            driver.execute_cdp_cmd('Network.setCookies', {'cookies': payload})
            added = len(payload)
        except Exception as exc:
            logger.debug('[落地] CDP 注入失败，退回逐条尝试：%s', str(exc)[:120])
            for cookie in cookies:
                try:
                    driver.execute_cdp_cmd('Network.setCookie', cookie)
                    added += 1
                except Exception:
                    continue
        logger.info('[落地] 已注入 %d/%d 个 Cookie，重新加载页面', added, len(cookies))
        driver.get('https://chatgpt.com/')
        dwell = max(10, int(dwell_seconds or 45))
        deadline = time.time() + dwell
        while time.time() < deadline:
            time.sleep(5)
        title = ''
        try:
            title = str(getattr(driver, 'title', '') or '')[:60]
        except Exception:
            pass
        logged_in = None
        try:
            logged_in = driver.execute_async_script(_SESSION_PROBE_JS)
        except Exception as exc:
            logger.debug('[落地] 登录态复核失败：%s', str(exc)[:100])
        logger.info('[落地] 完成 profile=%s title=%s logged_in=%s 用时=%.0fs',
                    profile_name, title, logged_in, time.time() - started)
        return {
            'ok': bool(logged_in) if logged_in is not None else bool(added),
            'profile': profile_name,
            'cookies_added': added,
            'cookies_total': len(cookies),
            'dwell_seconds': dwell,
            'title': title,
            'logged_in': logged_in,
            'duration_seconds': round(time.time() - started, 1),
        }
    except Exception as exc:
        logger.warning('[落地] 失败：%s: %s', type(exc).__name__, str(exc)[:160])
        return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
    finally:
        if driver is not None:
            try:
                close_cloak_driver(driver)
            except Exception:
                pass
