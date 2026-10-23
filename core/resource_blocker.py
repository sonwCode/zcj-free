# -*- coding: utf-8 -*-
"""注册浏览器的资源拦截（必须比 data_saver 更晚注册才生效）。

Playwright 同一模式的多个 route 是后注册先执行，data_saver 会先 continue 放行，
所以在 build_cloak_driver 里注册的拦截器永远轮不到。这里提供独立安装函数，
由注册流程在 data_saver.install_playwright() 之后调用。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SKIP_RTYPE = {'image', 'media', 'font'}
_SKIP_HOSTS = (
    'browser-intake-datadoghq.com',
    'ab.chatgpt.com',
    'statsig',
    'sentry.io',
    'segment.io',
    'google-analytics.com',
)
_BUNDLE_HOSTS = ('chatgpt.com', 'oaistatic.com')


def install(context, *, block_bundles: bool = False) -> dict:
    """挂载拦截器；返回统计计数（会被闭包持续更新）。"""
    stats = {'aborted': 0, 'bundle': 0, 'all': 0}

    def _handle(route, request):
        stats['all'] += 1
        try:
            rtype = str(getattr(request, 'resource_type', '') or '').lower()
            url = str(getattr(request, 'url', '') or '').lower()
            if rtype in _SKIP_RTYPE or any(h in url for h in _SKIP_HOSTS):
                stats['aborted'] += 1
                route.abort()
                return
            if block_bundles and any(h in url for h in _BUNDLE_HOSTS) \
                    and (rtype in ('script', 'stylesheet')
                         or url.endswith('.js') or url.endswith('.css') or url.endswith('.map')):
                stats['aborted'] += 1
                stats['bundle'] += 1
                route.abort()
                return
        except Exception:
            pass
        route.continue_()

    context.route('**/*', _handle)
    logger.warning('[资源拦截] 已挂载 block_bundles=%s（在 data_saver 之后）', block_bundles)
    return stats
