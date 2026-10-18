# -*- coding: utf-8 -*-
"""低价号码池独立配置。

设计边界（见 SESSION_HANDOFF 第 35.11 节）：
  - 默认跟随普通接码平台链；填写 SMS_POOL_PROVIDERS 时才使用号码池专用覆盖列表。
  - 不设置小时/每日金额预算；累计花费只做展示，不作为停机条件。
  - 采购需求由账号库中"未接码的 free 账号"数量驱动。
  - 保留数量与生命周期安全边界：单号码人民币上限、预热池容量、并发数、有效期。
"""
from config.env_loader import env_bool, env_int, env_str, apply_env_overrides

# 总开关：默认关闭。关闭时 worker 不轮询、不采购。
SMS_POOL_ENABLED: bool = False

# 暂停新采购：只停止抢号，不影响正在等待短信的激活。
SMS_POOL_PAUSED: bool = False

# 号码池平台覆盖列表（逗号分隔）。留空时跟随 SMS_PROVIDER_CHAIN 中已启用的平台。
# 未配置 API Key 的平台会被自动跳过；填写后只使用这里列出的平台。
SMS_POOL_PROVIDERS: str = ""

# 后台轮询间隔（秒）。低于 30 秒会被强制抬高，避免打爆供应商接口。
SMS_POOL_POLL_INTERVAL: int = 120

# 单号码人民币硬上限。留空表示必须显式配置后才允许采购。
SMS_POOL_MAX_PRICE_CNY: str = ""

# 启用后，号码池只按具体号码的哈希记录和跳过近期被上游判定为 VoIP 的号码，
# 不会因为一个号码而排除整个国家；补跑时若上游返回 voip_phone_disallowed，当前激活仍会释放。
# 各平台没有统一的购号前线路类型字段，因此这不是购号前的绝对保证。
SMS_POOL_REJECT_VOIP_NUMBERS: bool = False

# 预热池容量：未分配号码最多保留多少个。实际值还会被 eligible 账号数限制。
SMS_POOL_PREWARM_SIZE: int = 2

# 并发上限：同时运行的号码池手机验证任务数。
SMS_POOL_WORKERS: int = 2

# 本地租约：已领取号码多久未更新状态就允许对账回收。
SMS_POOL_LEASE_SECONDS: int = 900

# 平台激活有效期（秒）：过期仍未分配的号码由对账自动释放。
# 缺少该值时，服务重启后未领取号码会一直停在 reserved 并继续占用供应商额度。
SMS_POOL_ACTIVATION_TTL_SECONDS: int = 1200

# 号码池服务代码与国家；留空时沿用 SMS_SERVICE / 动态随机国家。
SMS_POOL_SERVICE: str = "dr"
SMS_POOL_COUNTRY: str = ""

# 单次轮询最多采购多少个号码，避免一次请求打爆供应商。
SMS_POOL_MAX_ACQUIRE_PER_TICK: int = 2

# 是否定时复查号码池候选账号的可用性。未知/待补跑账号在分配号码前仍会
# 自动做一次查活；此开关仅控制已检查账号的周期性复查。
SMS_POOL_LIVE_CHECK_ENABLED: bool = False

# 定时复查间隔（秒），默认 6 小时。
SMS_POOL_LIVE_CHECK_INTERVAL: int = 21600

# worker 每轮最多提交的定时查活数，避免大账号库瞬间塞满后台队列。
SMS_POOL_LIVE_CHECK_BATCH: int = 20

apply_env_overrides(globals(), {
    "SMS_POOL_ENABLED": "bool",
    "SMS_POOL_PAUSED": "bool",
    "SMS_POOL_PROVIDERS": "str",
    "SMS_POOL_POLL_INTERVAL": "int",
    "SMS_POOL_MAX_PRICE_CNY": "str",
    "SMS_POOL_REJECT_VOIP_NUMBERS": "bool",
    "SMS_POOL_PREWARM_SIZE": "int",
    "SMS_POOL_WORKERS": "int",
    "SMS_POOL_LEASE_SECONDS": "int",
    "SMS_POOL_ACTIVATION_TTL_SECONDS": "int",
    "SMS_POOL_SERVICE": "str",
    "SMS_POOL_COUNTRY": "str",
    "SMS_POOL_MAX_ACQUIRE_PER_TICK": "int",
    "SMS_POOL_LIVE_CHECK_ENABLED": "bool",
    "SMS_POOL_LIVE_CHECK_INTERVAL": "int",
    "SMS_POOL_LIVE_CHECK_BATCH": "int",
})
