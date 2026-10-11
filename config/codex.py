# -*- coding: utf-8 -*-
"""
注册成功后自动跑 Codex OAuth 授权的配置项。
设置 ENABLE_CODEX = False 可完全跳过此步骤。

参数来源：CLIProxyAPI 源码 internal/auth/codex/openai_auth.go + pkce.go，
对照 https://github.com/router-for-me/CLIProxyAPI 逐行确认。
"""
from config.env_loader import env_str, apply_env_overrides


# 兼容旧配置名；注册流程由 ENABLE_CODEX_AUTO 控制。
ENABLE_CODEX: bool = True

# Codex OAuth 客户端 ID（固定值，来自 CLIProxyAPI openai_auth.go:27 ClientID）
CODEX_CLIENT_ID: str = "app_EMoamEEZ73f0CkXaXp7hrann"

# 授权端点（openai_auth.go:25 AuthURL）
CODEX_AUTH_URL: str = "https://auth.openai.com/oauth/authorize"

# 换 token 端点（openai_auth.go:26 TokenURL）
CODEX_TOKEN_URL: str = "https://auth.openai.com/oauth/token"

# 回调地址（openai_auth.go:28 RedirectURI）
# 注意：本地并不真的起这个 server，只用来拦截重定向并从 Location 提取 code。
CODEX_REDIRECT_URI: str = "http://localhost:1455/auth/callback"

# OAuth scopes（openai_auth.go:75 GenerateAuthURL 里的 scope）
CODEX_SCOPE: str = "openid email profile offline_access"

# 输出目录名（仅名字，运行时拼到项目根；与 OUTLOOK_ACCOUNTS_FILE 同级风格）
CODEX_OUTPUT_DIRNAME: str = "codex_accounts"

# 请求超时（秒）
CODEX_REQUEST_TIMEOUT: int = 30


# ============================================================
# Codex 授权方式（2026-06-15 改造）
#
# 旧方案"复用注册的已登录 session"会撞 /choose-an-account 卡死；
# 新方案用全新干净 session 从头登录，走 OpenAI 标准风控路径
# （邮箱 OTP → 手机短信验证 → 选 workspace → 拿 code），
# 手机验证通过 SMSBower 或 Tiger SMS 自动取号和收码。
# ============================================================

# 注册成功后是否自动跑 Codex 授权。
ENABLE_CODEX_AUTO: bool = True

# 注册任务必须完成 Codex；关闭自动授权时也会标记为部分成功并允许补跑，
# 不再把 skipped 伪装成完整成功。
CODEX_REQUIRED_ON_REGISTRATION: bool = True

# Codex OAuth 授权驱动：
#   "protocol" = 原有 curl_cffi 协议授权
#   "roxy"     = 调用 RoxyBrowser 指纹浏览器完成授权页面/手机验证/回调捕获
#   "cloak"       = 调用 CloakBrowser 完成授权页面/手机验证/回调捕获
#   "browser_use" = 调用 Browser Use Cloud 完成授权页面/手机验证/回调捕获
#   "same_as_registration" = 跟随 REGISTRATION_DRIVER
# 支持逗号分隔的降级链，例如 "cloak,browser_use,roxy,protocol"：
# 按序尝试，遇到资源/配额类错误（如 Roxy 窗口额度不足）自动切换到下一个驱动，
# 不再让单个驱动的额度耗尽或故障拖垮整轮补跑。
CODEX_OAUTH_DRIVER: str = "roxy"

# 是否启用驱动降级链（False 时只用 CODEX_OAUTH_DRIVER 指定的驱动）
CODEX_OAUTH_DRIVER_FALLBACK: bool = True




# ============================================================
# CPA 管理接口（Codex 授权地址由 CPA 生成，本地只负责跑登录并提交回调）
# ============================================================

# 授权地址来源：
#   "cpa"   = 通过 CPA 管理接口 /v0/management/codex-auth-url 生成（推荐）
#   "sub2"  = 通过 sub2 管理接口生成，并把 callback 上传到 sub2
#   "local" = 使用本模块保留的本地 PKCE 生成逻辑（兼容旧方案）
CODEX_AUTH_URL_SOURCE: str = "cpa"

# CPA 管理页面或服务地址，例如 http://localhost:8317/admin/oauth
# 实际请求会取 origin，调用：
#   GET  /v0/management/codex-auth-url
#   POST /v0/management/oauth-callback
CPA_MANAGEMENT_URL: str = "http://127.0.0.1:8317/management.html"#/oauth"

# CPA 管理密钥，同时作为 Authorization: Bearer 和 X-Management-Key
CPA_MANAGEMENT_KEY: str = env_str("CPA_MANAGEMENT_KEY", "")

# CPA 管理接口请求超时（秒）
CPA_REQUEST_TIMEOUT: int = 30

# CPA 管理接口传输层瞬时失败（TLS 握手中断 / 连接重置 / 超时）的重试次数与基础间隔。
# 这类错误发生在握手阶段，重连一次通常即可恢复；不重试会让一次网络抖动
# 直接终止整轮 Codex 授权。
CPA_REQUEST_RETRIES: int = 3
CPA_REQUEST_RETRY_DELAY: int = 3

# 提交 OAuth callback 给 CPA 的重试次数/基础间隔。
# 遇到 409 Timeout waiting for OAuth callback、网络超时或 5xx 时，会按同一个 callback URL 重试。
CPA_CALLBACK_SUBMIT_RETRIES: int = 5
CPA_CALLBACK_SUBMIT_RETRY_DELAY: int = 6

# CPA 未返回完整 auth json 时，是否仍在本地 codex_accounts/ 记录一份回调提交凭据
CPA_SAVE_CALLBACK_RECEIPT: bool = True

# ============================================================
# 接码平台（SMSBower + Tiger SMS）
# ============================================================

# 兼容旧配置：SMS_PROVIDER 仍表示单平台模式下的首选平台。
SMS_PROVIDER: str = "smsbower"
# 有序接码链：前一个平台没有库存、余额或瞬时故障时才切到下一个；同一尝试绝不并发取号。
# 未配置 API Key 的平台会被自动跳过。留空时退回 SMS_PROVIDER 单平台模式。
SMS_PROVIDER_CHAIN: str = env_str("SMS_PROVIDER_CHAIN", "")

# SMSBower handler_api 配置
SMSBOWER_API_BASE: str = "https://smsbower.page/stubs/handler_api.php"
SMSBOWER_API_KEY: str = env_str("SMSBOWER_API_KEY", "")
SMSBOWER_USE_V2: bool = False
SMSBOWER_PROVIDER_IDS: str = ""
SMSBOWER_EXCEPT_PROVIDER_IDS: str = ""
SMSBOWER_PHONE_EXCEPTION: str = ""
# 兼容旧配置：实时汇率不可用时，按 1 USD = 7.2 CNY 回退。
SMSBOWER_USD_CNY_RATE: str = "7.2"
SMSBOWER_MIN_PRICE: str = ""

# Tiger SMS handler_api 配置（价格接口和 maxPrice 均使用 USD）
TIGER_SMS_API_BASE: str = "https://api.tiger-sms.com/stubs/handler_api.php"
TIGER_SMS_API_KEY: str = env_str("TIGER_SMS_API_KEY", "")
TIGER_SMS_USE_V2: bool = True
TIGER_SMS_PROVIDER_IDS: str = ""
TIGER_SMS_EXCEPT_PROVIDER_IDS: str = ""
TIGER_SMS_RANDOM_COUNTRY: bool = True
TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS: int = 12

# 服务代码：OpenAI (ChatGPT) = "dr"
SMS_SERVICE: str = "dr"

# 两个平台均使用外部平台国家代码；随机模式开启时留空，按实时库存随机选择。
SMS_COUNTRY: str = ""

# 通用人民币价格上限；程序实时读取 CNY -> USD 汇率后发送给两个平台。
SMS_MAX_PRICE: str = ""
# 实时汇率源，按顺序级联尝试，任一成功即用。
# 旧默认 api.frankfurter.app 已失效（返回 403/301），保留在末尾仅作兼容。
SMS_FX_RATE_URLS: str = (
    "https://open.er-api.com/v6/latest/CNY"
    ",https://api.frankfurter.app/latest?from=CNY&to=USD"
)
# 单源兼容项：非空时优先于 SMS_FX_RATE_URLS（便于临时指定单一来源）。
SMS_FX_RATE_URL: str = ""
SMS_FX_RATE_TTL: int = 900
# 最近一次成功获取的实时汇率（USD/CNY，即 1 USD = N CNY）。用于网络不可用时的
# 回退，使价格边界仍贴近市价，而不是钉死在一个陈旧常数上。
SMS_LAST_KNOWN_USD_CNY_RATE: str = "7.2"

# SMSBower 随机国家与号码质量策略
SMSBOWER_RANDOM_COUNTRY: bool = True
SMSBOWER_RANDOM_COUNTRY_ATTEMPTS: int = 12
SMS_NUMBER_ACQUIRE_RETRIES: int = 3
# 供应商价格/库存接口偶尔返回瞬时空快照；预检空快照时有限重查，不申请号码。
SMS_PREFLIGHT_RETRIES: int = 2
SMS_PREFLIGHT_RETRY_DELAY: int = 2
SMS_NUMBER_REJECT_TTL: int = 1800
# 仅明确 voip_phone_disallowed 的 provider-country 使用该冷却时间。
SMS_COUNTRY_REJECT_TTL: int = 900
SMS_TIER_FAILURE_THRESHOLD: int = 2
SMS_TIER_COOLDOWN_SECONDS: int = 2700

# 一个号收不到短信/被拒时，换号重试的最大次数
SMS_MAX_RETRIES: int = 10
# OpenAI fraud_guard 会要求“稍后再试”；连续换号过快会继续触发同一风控窗口。
# 仅 fraud_guard 使用这组退避，普通号码错误仍使用 3-8 秒的短等待。
SMS_FRAUD_GUARD_RETRY_MIN: int = 20
SMS_FRAUD_GUARD_RETRY_MAX: int = 45

# 单个号等待短信的最长秒数（超时则取消该号换下一个）
SMS_CODE_WAIT: int = 120

# 轮询接码平台查短信的间隔（秒）
SMS_POLL_INTERVAL: int = 5

# 接码平台 HTTP 请求超时（秒）
SMS_REQUEST_TIMEOUT: int = 30
# 释放/关闭激活的同步重试，避免瞬时网络错误让号码继续计费。
SMS_RELEASE_RETRIES: int = 3
SMS_RELEASE_RETRY_DELAY: int = 1

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {'ENABLE_CODEX_AUTO': 'bool', 'CODEX_REQUIRED_ON_REGISTRATION': 'bool', 'CODEX_OAUTH_DRIVER': 'str', 'CODEX_OAUTH_DRIVER_FALLBACK': 'bool', 'CODEX_AUTH_URL_SOURCE': 'str', 'CPA_MANAGEMENT_URL': 'str', 'CPA_MANAGEMENT_KEY': 'str', 'CPA_REQUEST_TIMEOUT': 'int', 'CPA_REQUEST_RETRIES': 'int', 'CPA_REQUEST_RETRY_DELAY': 'int', 'CPA_CALLBACK_SUBMIT_RETRIES': 'int', 'CPA_CALLBACK_SUBMIT_RETRY_DELAY': 'int', 'CPA_SAVE_CALLBACK_RECEIPT': 'bool', 'SMS_PROVIDER': 'str', 'SMS_PROVIDER_CHAIN': 'str', 'SMS_COUNTRY': 'str', 'SMS_SERVICE': 'str', 'SMS_MAX_PRICE': 'str', 'SMS_FX_RATE_URL': 'str', 'SMS_FX_RATE_URLS': 'str', 'SMS_FX_RATE_TTL': 'int', 'SMS_LAST_KNOWN_USD_CNY_RATE': 'str', 'SMS_MAX_RETRIES': 'int', 'SMS_FRAUD_GUARD_RETRY_MIN': 'int', 'SMS_FRAUD_GUARD_RETRY_MAX': 'int', 'SMS_CODE_WAIT': 'int', 'SMS_POLL_INTERVAL': 'int', 'SMS_REQUEST_TIMEOUT': 'int', 'SMS_RELEASE_RETRIES': 'int', 'SMS_RELEASE_RETRY_DELAY': 'int', 'SMSBOWER_API_BASE': 'str', 'SMSBOWER_API_KEY': 'str', 'SMSBOWER_USE_V2': 'bool', 'SMSBOWER_PROVIDER_IDS': 'str', 'SMSBOWER_EXCEPT_PROVIDER_IDS': 'str', 'SMSBOWER_PHONE_EXCEPTION': 'str', 'SMSBOWER_USD_CNY_RATE': 'str', 'SMSBOWER_MIN_PRICE': 'str', 'SMSBOWER_RANDOM_COUNTRY': 'bool', 'SMSBOWER_RANDOM_COUNTRY_ATTEMPTS': 'int', 'TIGER_SMS_API_BASE': 'str', 'TIGER_SMS_API_KEY': 'str', 'TIGER_SMS_USE_V2': 'bool', 'TIGER_SMS_PROVIDER_IDS': 'str', 'TIGER_SMS_EXCEPT_PROVIDER_IDS': 'str', 'TIGER_SMS_RANDOM_COUNTRY': 'bool', 'TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS': 'int', 'SMS_NUMBER_ACQUIRE_RETRIES': 'int', 'SMS_PREFLIGHT_RETRIES': 'int', 'SMS_PREFLIGHT_RETRY_DELAY': 'int', 'SMS_NUMBER_REJECT_TTL': 'int', 'SMS_COUNTRY_REJECT_TTL': 'int', 'SMS_TIER_FAILURE_THRESHOLD': 'int', 'SMS_TIER_COOLDOWN_SECONDS': 'int'})
