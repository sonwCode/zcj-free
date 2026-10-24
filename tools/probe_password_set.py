#!/usr/bin/env python3
"""探测：登录后设置密码的入口（只读，不提交任何修改）。"""
import json, sys, time
sys.path.insert(0, '/opt/turb-gpt-register')

import config as cfg
cfg.reload_all()

from core import db
from core.cloakbrowser_driver import build_cloak_driver, close_cloak_driver


def main():
    email = sys.argv[1] if len(sys.argv) > 1 else ''
    acc = db.get_account_by_email(email) if email else None
    if not acc:
        print('账号不存在'); return 1
    driver = None
    try:
        driver, info = build_cloak_driver(proxy=None, profile_name='probe-pw-' + email.replace('@','-'))
        targets = [
            'https://chatgpt.com/settings/security',
            'https://chatgpt.com/settings/account',
            'https://auth.openai.com/settings',
        ]
        for url in targets:
            try:
                driver.get(url)
                time.sleep(3)
                cur = driver.current_url
                title = driver.title
                # 只统计页面可见关键字，不提交
                markers = driver.execute_script("""
                  const txt = (document.body ? document.body.innerText : '').toLowerCase();
                  const inputs = document.querySelectorAll('input');
                  return {
                    len: txt.length,
                    has_pwd: txt.indexOf('password') >= 0,
                    has_set: txt.indexOf('set password') >= 0 || txt.indexOf('create password') >= 0,
                    inputs: inputs.length,
                    types: Array.from(inputs).slice(0,8).map(i => i.type || ''),
                  };
                """)
                print('URL=%s' % url)
                print('  final=%s' % cur)
                print('  title=%s' % (title or '')[:60])
                print('  markers=%s' % json.dumps(markers, ensure_ascii=False))
            except Exception as exc:
                print('URL=%s -> ERR %s: %s' % (url, type(exc).__name__, str(exc)[:80]))
    finally:
        if driver is not None:
            try: close_cloak_driver(driver)
            except Exception: pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
