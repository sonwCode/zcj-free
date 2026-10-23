# -*- coding: utf-8 -*-
"""
注册基础信息（默认值）

CLI 走 main.py 时会优先读这里；Web 控制台批量注册时也会用同样的默认值。
留空字段会触发交互式输入或自动生成（仅 USE_EMAIL_SERVICE=True 时邮箱会从 Outlook 池领取）。
"""
from config.env_loader import apply_env_overrides

# 注册邮箱（留空 + USE_EMAIL_SERVICE=True 时从 Outlook 池领取）
REGISTER_EMAIL = ""

# 注册密码（OTP-only 流程已不需要，留作备用）
REGISTER_PASSWORD = ""

# 用户名（注册完成后设置的显示名称，留空会自动生成 "Foo Bar" 形式）
# OpenAI 限制：name_invalid_chars —— 只允许字母和空格
REGISTER_NAME = ""

# 注册成功落库后是否自动查询套餐/Plus 资格。
# 关闭后不会在注册完成后立刻访问 backend-api/accounts/check，后续可在账号列表手动查询。
AUTO_PLAN_CHECK_AFTER_REGISTER = False

# 纯协议注册指纹生命周期：
#   False = 每次注册任务创建全新的 device/session/浏览器画像（默认）
#   True  = 同一邮箱重复注册或补跑时复用稳定指纹；不同邮箱仍彼此独立
PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL = False

# 注册成功并拿到 accessToken 后，在浏览器里随机停留一段时间再关闭连接。
# 格式：最小秒,最大秒。设为 "0,0" 表示不额外停留。
POST_REGISTER_DWELL_SECONDS_RANGE = "5,15"

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'REGISTER_EMAIL': 'str',
    'REGISTER_NAME': 'str',
    'AUTO_PLAN_CHECK_AFTER_REGISTER': 'bool',
    'PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL': 'bool',
    'POST_REGISTER_DWELL_SECONDS_RANGE': 'str',
})

# 协议注册成功后是否做一次"暖号"（完整 bootstrap + 会话初始化），
# 让账号留下真实使用痕迹，降低被清理概率。
PROTOCOL_WARMUP_AFTER_REGISTER: bool = True

# 协议注册慢速串行：同一时刻只跑一个协议注册，并与上一次保持最小间隔。
# 目的是避免同一出口/同一分钟内批量建号被关联。
PROTOCOL_SERIAL_MODE: bool = True
PROTOCOL_SERIAL_MIN_INTERVAL_SECONDS: int = 90

# 每个账号尽量使用不同的代理出口，并记住最近用过的几个。
PROTOCOL_ROTATE_EXIT: bool = True
PROTOCOL_RECENT_EXIT_MEMORY: int = 6

# 协议建号后是否用真实指纹浏览器"落地"一次（注入 Cookie、加载完整前端），
# 每个账号使用独立且持久化的 profile 目录。
PROTOCOL_BROWSER_SEAL: bool = False
PROTOCOL_BROWSER_SEAL_DWELL_SECONDS: int = 45

# 协议注册的真人节奏：各阶段之间插入随机停顿（秒区间），0 表示不停顿
PROTOCOL_HUMAN_PACING: bool = False
PROTOCOL_PACING_STEP_RANGE: str = "4,9"

# 每个阶段完成后停顿的秒区间（模拟真人节奏），空=不停顿
PROTOCOL_PHASE_PAUSE: str = ""
# 重放浏览器 Datadog RUM beacon（HAR 路径），空=不重放
PROTOCOL_DATADOG_HAR: str = ""
PROTOCOL_DATADOG_LIMIT: int = 60
