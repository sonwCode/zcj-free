# -*- coding: utf-8 -*-
"""CloakBrowser 自动化注册配置。"""
from config.env_loader import apply_env_overrides

# 是否无头启动：False=显示窗口，True=无头。
CLOAK_HEADLESS: bool = True

# 是否启用 CloakBrowser humanize 行为。
CLOAK_HUMANIZE: bool = True

# 使用当前出口 IP 自动匹配时区/语言/WebRTC IP。
CLOAK_GEOIP: bool = True

# 显式指定 Cloak 语言/时区；留空则在 CLOAK_GEOIP=True 时按出口 IP 自动推断。
# 例如：CLOAK_LOCALE="ja-JP"，CLOAK_TIMEZONE="Asia/Tokyo"。
CLOAK_LOCALE: str = ""
CLOAK_TIMEZONE: str = ""

# 是否把本项目传入/代理池抽取的代理传给 CloakBrowser。
CLOAK_USE_PROXY: bool = True

# Pro license；留空则使用免费 binary。
CLOAK_LICENSE_KEY: str = ""

# 固定指纹 seed；留空则每次 launch 随机生成新指纹。
CLOAK_FINGERPRINT_SEED: str = ""

# 持久化用户目录；留空则临时上下文。若要固定账号画像/缓存，可填如 "./cloak-profiles/default"。
CLOAK_USER_DATA_DIR: str = "/opt/turb-gpt-register/cloak-profiles"

# 额外 Chromium 参数，例如 ["--fingerprint=12345"]。CLOAK_FINGERPRINT_SEED 会自动追加。
CLOAK_EXTRA_ARGS: list = []

# 与原 Roxy Selenium 流程共用的超时时间。
CLOAK_SELENIUM_TIMEOUT: int = 90

# 首个登录页遭遇代理隧道/连接错误时，换出口重建浏览器的总尝试次数。
CLOAK_PROXY_BOOTSTRAP_RETRIES: int = 5

# 代理池所有凭据都不可用时，允许最后用服务器直连启动浏览器。
CLOAK_DIRECT_FALLBACK_ON_PROXY_FAILURE: bool = True

# 调试时保留浏览器不自动关闭。
CLOAK_KEEP_BROWSER_OPEN: bool = False

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {'CLOAK_HEADLESS': 'bool', 'CLOAK_HUMANIZE': 'bool', 'CLOAK_GEOIP': 'bool', 'CLOAK_LOCALE': 'str', 'CLOAK_TIMEZONE': 'str', 'CLOAK_USE_PROXY': 'bool', 'CLOAK_LICENSE_KEY': 'str', 'CLOAK_FINGERPRINT_SEED': 'str', 'CLOAK_USER_DATA_DIR': 'str', 'CLOAK_SELENIUM_TIMEOUT': 'int', 'CLOAK_PROXY_BOOTSTRAP_RETRIES': 'int', 'CLOAK_DIRECT_FALLBACK_ON_PROXY_FAILURE': 'bool', 'CLOAK_KEEP_BROWSER_OPEN': 'bool'})

# 注册浏览器流量裁剪：拦截无用资源，降低单号流量。
# 只拦"不携带账号信号"的东西：图片/媒体/字体、埋点与统计域名。
CLOAK_BLOCK_RESOURCE_TYPES: str = "image,media,font"
CLOAK_BLOCK_HOSTS: str = "browser-intake-datadoghq.com,ab.chatgpt.com,statsig,sentry.io,segment.io,google-analytics.com"
CLOAK_BLOCK_RESOURCES: bool = True

# 注册时是否拦截 chatgpt.com 的前端应用包（Next.js chunks / css / map）。
# 这些是 SPA 资源，注册用不到，但能占单号流量的 60% 以上。
CLOAK_BLOCK_APP_BUNDLES: bool = False

# 精简入口：用 API 构造 authorize URL 并注入 Cookie，直接进 auth.openai.com，
# 跳过 chatgpt.com/auth/login 的整个前端应用包（约省 13MB）。
CLOAK_LEAN_ENTRY: bool = False

# 录制注册过程 HAR（用于比对协议请求头/参数），空=不录制
CLOAK_RECORD_HAR_DIR: str = ""
