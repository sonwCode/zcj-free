# -*- coding: utf-8 -*-
"""
2FA（TOTP）配置

是否在注册成功后自动设置 2FA：
    True:  注册完成 → 拉新 OTP 邮件 → enroll TOTP → activate → 把 secret 写入 DB
    False: 跳过整个 2FA 流程，只保存 邮箱 + accessToken

关掉 2FA 不会影响账号可用性，仅意味着账号没有动态口令保护，且少收一封 OTP 邮件。
"""
from config.env_loader import apply_env_overrides

ENABLE_2FA = False

# 2FA 网络代理模式：
#   saved = 优先使用账号保存的有效代理（无有效代理时回退代理池）
#   pool  = 忽略账号保存的代理，每次任务都从 PROXY_POOL 随机抽取
TWOFA_PROXY_MODE = "saved"
# 初始重认证的保存代理失败后使用的独立备用代理池；为空时回退全局 PROXY_POOL。
# 该池只用于 2FA，不参与注册和短信流程。
TWOFA_PROXY_FALLBACK_POOL: list[str] = []

# 发起 reauth（CSRF + signin）时的临时网络错误重试。403 会先清理当前会话的
# 本地熔断，再按指数退避重试；业务类 4xx 不重试。
TWOFA_REAUTH_MAX_ATTEMPTS = 3
TWOFA_REAUTH_RETRY_DELAY = 3.0
# 保存代理在初始 CSRF/signin 重认证阶段连续 403 时，切换代理池重试一次。
TWOFA_REAUTH_PROXY_FALLBACK = True
TWOFA_REAUTH_PROXY_FALLBACK_ATTEMPTS = 1

# 注册后置流程开启 2FA 时，Codex 必须等待 2FA 终态；超时后保留账号并阻止本轮 Codex。
TWOFA_PRE_CODEX_TIMEOUT_SECONDS = 900

# 2FA 后台队列。workers 是实际同时执行的账号数，修改后需重启进程以重建线程池。
TWOFA_WORKERS = 4
TWOFA_QUEUE_LIMIT = 200

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'ENABLE_2FA': 'bool',
    'TWOFA_PROXY_MODE': 'str',
    'TWOFA_PROXY_FALLBACK_POOL': 'list_str_multiline',
    'TWOFA_REAUTH_MAX_ATTEMPTS': 'int',
    'TWOFA_REAUTH_RETRY_DELAY': 'float',
    'TWOFA_REAUTH_PROXY_FALLBACK': 'bool',
    'TWOFA_REAUTH_PROXY_FALLBACK_ATTEMPTS': 'int',
    'TWOFA_PRE_CODEX_TIMEOUT_SECONDS': 'int',
    'TWOFA_WORKERS': 'int',
    'TWOFA_QUEUE_LIMIT': 'int',
})
