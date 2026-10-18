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
# 接码平台（SMSBower + Tiger SMS + HeroSMS + SMSPool + Vak SMS）
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
# 兼容旧配置：新配置应按平台填写 *_EXCEPT_REGIONS。
SMS_EXCEPT_REGIONS: str = env_str("SMS_EXCEPT_REGIONS", "")
SMSBOWER_EXCEPT_REGIONS: str = ""
# 旧配置名，仅为兼容旧 .env 保留；不再作为固定汇率回退。
SMSBOWER_USD_CNY_RATE: str = ""
SMSBOWER_MIN_PRICE: str = ""

# Tiger SMS handler_api 配置（价格接口和 maxPrice 均使用 USD）
TIGER_SMS_API_BASE: str = "https://api.tiger-sms.com/stubs/handler_api.php"
TIGER_SMS_API_KEY: str = env_str("TIGER_SMS_API_KEY", "")
TIGER_SMS_USE_V2: bool = True
TIGER_SMS_PROVIDER_IDS: str = ""
TIGER_SMS_EXCEPT_PROVIDER_IDS: str = ""
TIGER_SMS_EXCEPT_REGIONS: str = ""
TIGER_SMS_RANDOM_COUNTRY: bool = True
TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS: int = 12

# HeroSMS：SMS-Activate 兼容 handler_api；未填写 key 时自动跳过。
HEROSMS_API_BASE: str = "https://hero-sms.com/stubs/handler_api.php"
HEROSMS_API_KEY: str = env_str("HEROSMS_API_KEY", "")
HEROSMS_USE_V2: bool = False
HEROSMS_SERVICE: str = ""
HEROSMS_COUNTRY: str = ""
# 可选的 USD 额外上限；与通用 SMS_MAX_PRICE 取更低值。
HEROSMS_MAX_PRICE: str = ""
HEROSMS_PROVIDER_IDS: str = ""
HEROSMS_EXCEPT_PROVIDER_IDS: str = ""
HEROSMS_PHONE_EXCEPTION: str = ""
HEROSMS_EXCEPT_REGIONS: str = ""

# SMSPool 原生 API；国家/服务代码必须按 SMSPool 控制台配置填写。
SMSPOOL_API_BASE: str = "https://api.smspool.net"
SMSPOOL_API_KEY: str = env_str("SMSPOOL_API_KEY", "")
SMSPOOL_SERVICE: str = ""
SMSPOOL_COUNTRY: str = ""
# 可选的 USD 额外上限；与通用 SMS_MAX_PRICE 取更低值。
SMSPOOL_MAX_PRICE: str = ""
SMSPOOL_MIN_PRICE: str = ""
SMSPOOL_PRICING_OPTION: str = "0"
SMSPOOL_POOL: str = ""
SMSPOOL_EXCEPT_REGIONS: str = ""

# Vak SMS：SMS-Activate 兼容 handler_api；国家/服务代码按 Vak 文档填写。
VAKSMS_API_BASE: str = "https://vak-sms.com/stubs/handler_api.php"
VAKSMS_API_KEY: str = env_str("VAKSMS_API_KEY", "")
VAKSMS_USE_V2: bool = False
VAKSMS_SERVICE: str = ""
VAKSMS_COUNTRY: str = ""
# 可选的 USD 额外上下限；与通用 SMS_MAX_PRICE 取更严格范围。
VAKSMS_MAX_PRICE: str = ""
VAKSMS_MIN_PRICE: str = ""
VAKSMS_OPERATOR: str = ""
VAKSMS_REF: str = ""
VAKSMS_FIXED_PRICE: bool = False
VAKSMS_EXCEPT_REGIONS: str = ""

# 服务代码：OpenAI (ChatGPT) = "dr"
SMS_SERVICE: str = "dr"

# 国家代码；留空且开启随机模式时按价格和实时库存动态选择。
SMS_COUNTRY: str = ""

# 所有接码平台共用的价格硬上限，按人民币填写；程序按实时汇率换算成各平台
# 所需的 USD maxPrice。平台页的 provider-specific 价格只能进一步收紧，不能放宽此上限。
SMS_MAX_PRICE: str = ""

# 启用后，取号时会优先跳过质量账本中同一号码近期被上游判定为 VoIP 的记录，
# 不会因为一个号码而排除整个国家；上游明确返回 voip_phone_disallowed 时仍会释放当前号码。
# 各平台没有统一的购号前线路类型字段，因此这不是购号前的绝对保证。
SMS_REJECT_VOIP_NUMBERS: bool = False

# 实时汇率源，按顺序级联尝试，任一成功即用。
# 旧默认 api.frankfurter.app 已失效（返回 403/301），保留在末尾仅作兼容。
SMS_FX_RATE_URLS: str = (
    "https://open.er-api.com/v6/latest/CNY"
    ",https://api.frankfurter.app/latest?from=CNY&to=USD"
)
# 单源兼容项：非空时优先于 SMS_FX_RATE_URLS（便于临时指定单一来源）。
SMS_FX_RATE_URL: str = ""
SMS_FX_RATE_TTL: int = 600
# 最近一次成功获取的实时汇率（USD/CNY，即 1 USD = N CNY）。用于网络不可用时的
# 回退，使价格边界仍贴近市价；运行时也会写入 SMS_FX_CACHE_FILE。
SMS_LAST_KNOWN_USD_CNY_RATE: str = ""
# 进程重启后读取最近一次成功汇率；相对项目根目录解析。
SMS_FX_CACHE_FILE: str = "runtime/sms_fx_rate.json"

# SMSBower 随机国家与号码质量策略
SMSBOWER_RANDOM_COUNTRY: bool = True
SMSBOWER_RANDOM_COUNTRY_ATTEMPTS: int = 12
# 价格/库存快照为空时连续刷新的总时长（秒）。旧实现固定约 24 秒，库存恢复稍慢
# 就会在仍有号可买的情况下误报“没有可用国家”。
SMSBOWER_STOCK_WAIT_SECONDS: int = 90
SMS_NUMBER_ACQUIRE_RETRIES: int = 3
# 供应商价格/库存接口偶尔返回瞬时空快照；预检空快照时有限重查，不申请号码。
SMS_PREFLIGHT_RETRIES: int = 2
SMS_PREFLIGHT_RETRY_DELAY: int = 2
SMS_NUMBER_REJECT_TTL: int = 1800
# 兼容旧配置字段；VoIP 不按国家冷却，具体号码使用 SMS_NUMBER_REJECT_TTL。
SMS_COUNTRY_REJECT_TTL: int = 900
SMS_TIER_FAILURE_THRESHOLD: int = 2
SMS_TIER_COOLDOWN_SECONDS: int = 2700

# 短信质量账本只保存号码哈希和 provider/country 聚合统计，不保存原始号码。
SMS_QUALITY_FILE: str = "runtime/sms_quality.json"
SMS_QUALITY_MAX_AGE_DAYS: int = 30
SMS_QUALITY_MIN_SAMPLES: int = 3
SMS_QUALITY_MAX_ENTRIES: int = 5000

# 一个号收不到短信/被拒时，换号重试的最大次数
SMS_MAX_RETRIES: int = 10
# OpenAI fraud_guard 会要求“稍后再试”；连续换号过快会继续触发同一风控窗口。
# 仅 fraud_guard 使用这组退避，普通号码错误仍使用 3-8 秒的短等待。
SMS_FRAUD_GUARD_RETRY_MIN: int = 60
SMS_FRAUD_GUARD_RETRY_MAX: int = 120

# 单个号等待短信的最长秒数（超时则取消该号换下一个）
SMS_CODE_WAIT: int = 120

# Codex 登录邮箱 OTP 等待窗口；Remail 投递可能晚于首次页面渲染。
CODEX_EMAIL_OTP_WAIT: int = 120
CODEX_EMAIL_OTP_MAX_ATTEMPTS: int = 4
# Codex 在邮箱提交前遇到代理403/连接重置时，最多更换出口重试次数。
CODEX_PROXY_RETRY_ATTEMPTS: int = 2
# 批量 Codex 补跑仍使用线程池；每个 worker 在启动浏览器前错峰，避免同一秒
# 让多个授权页同时命中同一个出口/代理。设为 0 可恢复同时启动。
CODEX_RETRY_STAGGER_SECONDS: int = 8

# 轮询接码平台查短信的间隔（秒）
SMS_POLL_INTERVAL: int = 5

# 接码平台 HTTP 请求超时（秒）
SMS_REQUEST_TIMEOUT: int = 30
# 释放/关闭激活的同步重试，避免瞬时网络错误让号码继续计费。
SMS_RELEASE_RETRIES: int = 3
SMS_RELEASE_RETRY_DELAY: int = 1

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {'ENABLE_CODEX_AUTO': 'bool', 'CODEX_REQUIRED_ON_REGISTRATION': 'bool', 'CODEX_OAUTH_DRIVER': 'str', 'CODEX_OAUTH_DRIVER_FALLBACK': 'bool', 'CODEX_AUTH_URL_SOURCE': 'str', 'CPA_MANAGEMENT_URL': 'str', 'CPA_MANAGEMENT_KEY': 'str', 'CPA_REQUEST_TIMEOUT': 'int', 'CPA_REQUEST_RETRIES': 'int', 'CPA_REQUEST_RETRY_DELAY': 'int', 'CPA_CALLBACK_SUBMIT_RETRIES': 'int', 'CPA_CALLBACK_SUBMIT_RETRY_DELAY': 'int', 'CPA_SAVE_CALLBACK_RECEIPT': 'bool', 'SMS_PROVIDER': 'str', 'SMS_PROVIDER_CHAIN': 'str', 'SMS_COUNTRY': 'str', 'SMS_SERVICE': 'str', 'SMS_MAX_PRICE': 'str', 'SMS_REJECT_VOIP_NUMBERS': 'bool', 'SMS_FX_RATE_URL': 'str', 'SMS_FX_RATE_URLS': 'str', 'SMS_FX_RATE_TTL': 'int', 'SMS_LAST_KNOWN_USD_CNY_RATE': 'str', 'SMS_FX_CACHE_FILE': 'str', 'SMS_MAX_RETRIES': 'int', 'SMS_FRAUD_GUARD_RETRY_MIN': 'int', 'SMS_FRAUD_GUARD_RETRY_MAX': 'int', 'SMS_CODE_WAIT': 'int', 'CODEX_EMAIL_OTP_WAIT': 'int', 'CODEX_EMAIL_OTP_MAX_ATTEMPTS': 'int', 'CODEX_PROXY_RETRY_ATTEMPTS': 'int', 'CODEX_RETRY_STAGGER_SECONDS': 'int', 'SMS_POLL_INTERVAL': 'int', 'SMS_REQUEST_TIMEOUT': 'int', 'SMS_RELEASE_RETRIES': 'int', 'SMS_RELEASE_RETRY_DELAY': 'int', 'SMSBOWER_API_BASE': 'str', 'SMSBOWER_API_KEY': 'str', 'SMSBOWER_USE_V2': 'bool', 'SMSBOWER_PROVIDER_IDS': 'str', 'SMSBOWER_EXCEPT_PROVIDER_IDS': 'str', 'SMSBOWER_PHONE_EXCEPTION': 'str', 'SMSBOWER_EXCEPT_REGIONS': 'str', 'SMSBOWER_USD_CNY_RATE': 'str', 'SMSBOWER_MIN_PRICE': 'str', 'SMSBOWER_RANDOM_COUNTRY': 'bool', 'SMSBOWER_RANDOM_COUNTRY_ATTEMPTS': 'int', 'SMSBOWER_STOCK_WAIT_SECONDS': 'int', 'TIGER_SMS_API_BASE': 'str', 'TIGER_SMS_API_KEY': 'str', 'TIGER_SMS_USE_V2': 'bool', 'TIGER_SMS_PROVIDER_IDS': 'str', 'TIGER_SMS_EXCEPT_PROVIDER_IDS': 'str', 'TIGER_SMS_EXCEPT_REGIONS': 'str', 'TIGER_SMS_RANDOM_COUNTRY': 'bool', 'TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS': 'int', 'HEROSMS_API_BASE': 'str', 'HEROSMS_API_KEY': 'str', 'HEROSMS_USE_V2': 'bool', 'HEROSMS_SERVICE': 'str', 'HEROSMS_COUNTRY': 'str', 'HEROSMS_MAX_PRICE': 'str', 'HEROSMS_PROVIDER_IDS': 'str', 'HEROSMS_EXCEPT_PROVIDER_IDS': 'str', 'HEROSMS_PHONE_EXCEPTION': 'str', 'HEROSMS_EXCEPT_REGIONS': 'str', 'SMSPOOL_API_BASE': 'str', 'SMSPOOL_API_KEY': 'str', 'SMSPOOL_SERVICE': 'str', 'SMSPOOL_COUNTRY': 'str', 'SMSPOOL_MAX_PRICE': 'str', 'SMSPOOL_MIN_PRICE': 'str', 'SMSPOOL_PRICING_OPTION': 'str', 'SMSPOOL_POOL': 'str', 'SMSPOOL_EXCEPT_REGIONS': 'str', 'VAKSMS_API_BASE': 'str', 'VAKSMS_API_KEY': 'str', 'VAKSMS_USE_V2': 'bool', 'VAKSMS_SERVICE': 'str', 'VAKSMS_COUNTRY': 'str', 'VAKSMS_MAX_PRICE': 'str', 'VAKSMS_MIN_PRICE': 'str', 'VAKSMS_OPERATOR': 'str', 'VAKSMS_REF': 'str', 'VAKSMS_FIXED_PRICE': 'bool', 'VAKSMS_EXCEPT_REGIONS': 'str', 'SMS_NUMBER_ACQUIRE_RETRIES': 'int', 'SMS_PREFLIGHT_RETRIES': 'int', 'SMS_PREFLIGHT_RETRY_DELAY': 'int', 'SMS_NUMBER_REJECT_TTL': 'int', 'SMS_COUNTRY_REJECT_TTL': 'int', 'SMS_TIER_FAILURE_THRESHOLD': 'int', 'SMS_TIER_COOLDOWN_SECONDS': 'int', 'SMS_QUALITY_FILE': 'str', 'SMS_QUALITY_MAX_AGE_DAYS': 'int', 'SMS_QUALITY_MIN_SAMPLES': 'int', 'SMS_QUALITY_MAX_ENTRIES': 'int'})
apply_env_overrides(globals(), {'SMS_EXCEPT_REGIONS': 'str'})
