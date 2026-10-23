# 参考项目深度研读笔记

> 目的：逐个吃透用户提供的全部项目，找出我们项目的优化位置，而不是浅扫。
> 每条结论都标注来源文件，便于复核。

## 目录

1. [2951461586/GPT-Register-Tool](#1-2951461586gpt-register-tool)
2. [absoultly-classical/GPT-Register-Tool](#2-absoultly-classicalgpt-register-tool)
3. [myfanhua/turb-gpt-free-register](#3-myfanhuaturb-gpt-free-register)
4. [Haohuangsix/gpt-register-pipeline](#4-haohuangsixgpt-register-pipeline)
5. [其余项目（按规模递减）](#5-其余项目)

---

## 1. 2951461586/GPT-Register-Tool

规模：803 个 py 文件、24.1 万行，是本批项目里最完整的一个。
技术栈：Python + .NET（SmsWorkbench WPF 客户端）；协议注册 + 浏览器注册 + 支付/收款链路。

### 1.1 注册子系统分层（sms_tool/registration，30 个文件）

| 模块 | 职责 | 我们的对应 |
|---|---|---|
| `handlers.py` 56KB | 协议注册阶段编排（总流程） | `core/protocol_registration.py` |
| `otp_stages.py` 21KB | 邮箱验证码阶段 | 同上（内联） |
| `account_stages.py` 19KB | 建号、会话就绪、AT 探测 | 同上 + `account_liveness` |
| `retry_guard.py` 21KB | **跨批次重试熔断** | ❌ 缺失 |
| `pulse.py` 14KB | **批次脉冲调度 + IP 封禁检测** | ❌ 缺失 |
| `outcome.py` 17KB | 结果分类（把失败拆成可判定种类） | 部分（只有 success/failed） |
| `edge_challenge.py` 6KB | **流程内 CF 边缘挑战处理 + 换出口** | ❌ 缺失 |
| `preflight.py` 10KB | 注册网络预检（代理协议识别、边缘检查） | 部分（我们只做 TLS 预检） |
| `probe.py` 11KB | **只读探测邮箱是否已被 OpenAI 注册** | ❌ 缺失 |
| `checkpoint.py` + `resume.py` | 建号后断点 + 失败续跑 | ❌ 缺失 |
| `policy.py` | 共享重试判定 | 部分 |
| `concurrency.py` | 昂贵阶段组的并发闸门 | 部分（串行闸门） |
| `flags.py` | 单一开关读取点 | ❌ 我们开关散落各处 |
| `funnel.py` | 批次漏斗统计（注册/推广分母） | ❌ 缺失 |
| `reference_lane.py` | 参考顺序的密码注册泳道 | 参考 |

### 1.2 三个最值得抄的设计

**(a) 出口边缘探测（`proxy/edge_probe.py`）**

把「代理能连」与「ChatGPT 愿意服务这条出口」分成两个问题：

- `clean`：200/3xx，或到达源站返回 401/429 —— **只有这种能建号**
- `blocked`：403 或 HTML 挑战（`cf-chl` / `Just a moment` / `cf-mitigated: challenge`）
- `degraded`：5xx 等非明确拒绝
- `dead`：传输失败

判定词表是它最有价值的部分：**"能连通"≠"能注册"**。

**已落地**：我们实现了 `core/proxy_edge_probe.py` + `tools/probe_exits.py`，
并对协议注册加了预检（`PROTOCOL_PREFLIGHT_EXIT_PROBE`）。
实测当前 10 条出口在 chatgpt/auth/otp 三处边缘全部 clean。

**(b) 邮箱新颖性只读探测（`registration/probe.py`）**

用与注册相同的 `signin → authorize` 握手，只走到 authorize 落地就停：

- 落地 `/create-account[/password]` → `unregistered`
- 落地 `/log-in`、`login_password`、`mfa_challenge` → `registered`
- `/email-verification` 且无法判定 → `unknown`（**不猜**）

意义：注册前就能知道这个邮箱在 OpenAI 侧是否已经存在。
我们的邮箱池是 remail 池化地址，**很可能存在"地址已被用过"的情况**，
这会直接导致注册异常或低信任。这条我们还没做。

**(c) 流程内 CF 边缘挑战处理（`registration/edge_challenge.py`）**

在 `request_with_retry` 里挂一个 hook：命中边缘挑战 → **在同一池内换出口** →
记录 proxy_audit 计数 → 清 session 熔断。我们目前直接抛 `CloudflareChallengeError` 结束整轮。

### 1.3 其他可借鉴点

- `failure_registry.py`：失败词汇的唯一注册表（`OTP_BAN_MARKERS` 等），
  避免各模块各写一套字符串匹配。
- `pulse.py`：把"OTP 失败"读成**服务端没完成派发**而不是"码没到邮箱"；
  只有整轮一致的派发侧失败才算出口级证据。
- `browser_profile_reclaim.py`：浏览器持久 profile 的占用检测与回收
  （我们 `cloak-profiles/` 会累积，且进程异常退出会锁目录）。

### 1.4 我们的差距清单（按价值排序）

1. 邮箱新颖性探测（可能直接解释部分账号异常）
2. CF 边缘挑战时的出口轮换（我们现在的做法是整轮失败）
3. 失败分类体系（我们现在只有 success/failed/error 字符串）
4. 批次脉冲 + IP 封禁检测
5. profile 占用回收
6. 断点续跑（AT 探测 403 时不重走全流程）

---

## 2. absoultly-classical/GPT-Register-Tool

（下一轮补齐）

## 3. myfanhua/turb-gpt-free-register

（下一轮补齐）

## 4. Haohuangsix/gpt-register-pipeline

（下一轮补齐）

## 5. 其余项目

（后续补齐）
