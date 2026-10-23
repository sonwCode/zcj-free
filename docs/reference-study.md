# 参考项目深度研读笔记

> 目的：逐个吃透用户提供的全部项目，找出我们项目的优化位置，而不是浅扫。
> 每条结论都标注来源文件，便于复核。
> 注意：本文件以本地为准；同步到服务器时只能用 本地→服务器 方向。

## 目录

1. 2951461586/GPT-Register-Tool
2. absoultly-classical/GPT-Register-Tool
3. myfanhua/turb-gpt-free-register
4. Haohuangsix/gpt-register-pipeline
5. hyhang915/gptfree-register
6. Regert888/gpt-auto-register
7. 失败分类体系（2951461586 kernel 模块）

---

## 1. 2951461586/GPT-Register-Tool

规模：803 个 py 文件、24.1 万行，是本批项目里最完整的一个。
技术栈：Python + .NET（SmsWorkbench WPF 客户端）；协议注册 + 浏览器注册 + 支付链路。

### 1.1 注册子系统分层（sms_tool/registration，30 文件）

| 模块 | 职责 | 我们的对应 |
|---|---|---|
| handlers.py 56KB | 协议注册阶段编排 | core/protocol_registration.py |
| otp_stages.py 21KB | 邮箱验证码阶段 | 同上（内联） |
| account_stages.py 19KB | 建号、会话就绪、AT 探测 | 同上 + account_liveness |
| retry_guard.py 21KB | 跨批次重试熔断 | 已有（batch_pulse 部分覆盖） |
| pulse.py 14KB | 批次脉冲调度 + IP 封禁检测 | 已落地 core/batch_pulse.py |
| outcome.py 17KB | 结果分类 | 已落地 core/failure_registry.py |
| edge_challenge.py 6KB | 流程内 CF 挑战换出口 | 已落地 core/edge_retry.py |
| preflight.py 10KB | 注册网络预检 | 已落地 core/proxy_edge_probe.py |
| probe.py 11KB | 只读探测邮箱是否已注册 | 已落地 core/mailbox_probe.py |
| checkpoint.py + resume.py | 建号后断点 + 续跑 | ❌ 待做 |
| flags.py | 单一开关读取点 | ❌ 待做 |
| funnel.py | 批次漏斗统计 | 部分（failure_registry.summarize） |

### 1.2 出口边缘探测（proxy/edge_probe.py）

核心洞察：**"代理能连" ≠ "ChatGPT 愿意服务这条出口"**。

判定词表：clean（200/3xx 或源站 401/429）/ blocked（403 或 HTML 挑战）/
degraded（5xx）/ dead（传输失败）。

已落地：`core/proxy_edge_probe.py` + `tools/probe_exits.py`；
协议注册预检 `PROTOCOL_PREFLIGHT_EXIT_PROBE`。实测 10 出口 × 3 边缘全 clean。

### 1.3 邮箱新颖性只读探测（registration/probe.py）

只走到 authorize 落地就停，不发 OTP、不建号。
判定：/create-account → unregistered；/log-in、mfa → registered；
/email-verification → 参考实现返回 unknown。

**我们更进一步**：抓验证页 HTML 用 passwordless/signup 与 welcome back 两组标记，
把 unknown 变成可判定（实测新地址 → unregistered note=page-markers）。

已落地：`core/mailbox_probe.py`（默认走代理 + 跨出口重试）+ `tools/probe_mailbox.py`。

### 1.4 第 6 轮再挖掘：pulse / edge_challenge 的算法细节

**(a) 出口封禁判定必须"整轮一致"**

实测依据（批次 25288，wave_size=4）：
```
wave 2: 3 超时 + 1 拿到码 → 旧判据误判封禁，白停 60s×2
wave 4/5/6: 同一出口、同样配置 → 0 超时
```
原因：每个账号钉在池里各自出口上，任一账号拿到码即证明链路通。
正确判据 = 派发侧失败数 ≥ 阈值 **且** 整轮全是派发侧失败；
且必须区分派发侧（可能出口问题）与邮箱侧（换出口无意义）。

已落地：`core/batch_pulse.py` + 7 项测试。

**(b) 边缘挑战要换出口重来**

已落地：`core/edge_retry.py`（is_edge_challenge / run_with_edge_retry），
接入 registration_service 生产路径，5 项测试。

### 1.5 我们的差距清单

| 项 | 状态 |
|---|---|
| 出口边缘探测 | ✅ 已落地 |
| 邮箱新颖性探测 | ✅ 已落地（且更强） |
| CF 挑战换出口 | ✅ 已落地 |
| 批次封禁判定 | ✅ 已落地 |
| 失败分类体系 | ✅ 已落地（第 7 节） |
| profile 占用回收 | ❌ 待做 |
| 断点续跑 checkpoint/resume | ❌ 待做 |
| 单一开关读取点 flags | ❌ 待做 |

---

## 2. absoultly-classical/GPT-Register-Tool

规模：449 文件、12.9 万行。与项目 1 同源但更精简，没有 registration/ 分包。

### 2.1 sentinel 包的工程化

| 文件 | 内容 |
|---|---|
| bundle.py | 对 sdk.js / runner.js 做 **SHA256 固定**，版本常量 20260219f9f6，哈希不符即抛错 |
| client.py | SentinelToken(flow, device_id, token, so_token, challenge)；FLOW_PAGE_URLS |
| runner.py | Node 子进程适配层 |

它锁哈希的原因：SDK 换版本 → 旧 so/token 结构失效 → 注册**静默**变低信任。

### 2.2 我们做过的对照验证

```
本地 sdk.js 33820 字节 sha256=49d0284b…
线上 /sentinel/20260810913b/sdk.js 33820 字节 sha256=49d0284b…
一致: True（相似度 1.000）
```
结论：签名层没有落后；so 差异来自行为输入而非 SDK 版本。
（真实 SDK 在 /sentinel/{sv}/sdk.js，/backend-api/sentinel/sdk.js 只是 1.4KB 存根。）

### 2.3 已落地

`core/sentinel_provenance.py`（local_fingerprint / check_drift / log_drift_if_any）
+ `tools/sentinel_info.py`；注册时把 sv + sdk sha256 写入账号 extra.sentinel。

残留待做：账号恢复流程对照（accounts/account_recovery.py 62KB）。

---

## 3. myfanhua/turb-gpt-free-register

规模：147 个 py、5.3 万行。我们的代码基线。

### 3.1 模块差集（决定性方法）

文件树集合差显示：**它有我们没有的只有 10 个文件**，其中唯一有价值的：
`core/account_inspection_service.py`（账号巡检）+ 86KB 测试。
反之我们有 226 个它没有的文件（sms_pool / protocol / cloak 改造等）。

### 3.2 巡检的两层判定

```python
payload = decode_jwt_payload_unverified(token)   # 只读 JWT，不发请求
return payload["exp"] <= time.time()
```
1. 本地读 exp：过期=必然失效，不用浪费代理和查活；
2. 只有过期的号才入队查活。

配套：巡检状态持久化、进程租约、孤儿任务恢复、双线程池（stage/worker）。

### 3.3 我们实测出的两个加强点

```
本地巡检 23 个号 → 有效 18 / 已废跳过 5；JWT 剩余普遍 9.9 天
```
- **服务端吊销不改变 exp** → 本地巡检看不到，必须补轻量 /me 探活；
- **不重试会误判**：第一轮 unknown 5 个，复测后 4 个其实活着。

```
第一轮 alive 13 / revoked 5 / unknown 5
复测 unknown → alive 4 / revoked 1 / unknown 0
```

### 3.4 已落地

`core/account_inspection.py`（token_state / needs_refresh / inspect_local / inspect_network）
+ `tools/inspect_accounts.py`（--local / --network / --retry-unknown / --update-db）。

---

## 4. Haohuangsix/gpt-register-pipeline

规模：120 个 py、3.2 万行。核心价值是 `docs/protocol_fingerprint_har_analysis.md`
（236 个请求的完整指纹规范）。

### 4.1 p 数组 25 项语义（HAR 解码）

| 下标 | 含义 |
|---:|---|
| 0 | screen.width + screen.height |
| 1 | new Date().toString() |
| 2 | performance.memory.jsHeapSizeLimit |
| 3 | 初始/PoW attempt |
| 4 | UA |
| 5 | script src（被采样 JS 入口） |
| 6 | build id / data-build |
| 7 | navigator.language |
| 8 | navigator.languages.join(',')（单值） |
| 9 | 随机/耗时 |
| 10 | navigator 原型样本（createAuctionNonce…） |
| 11 | React key（__reactContainer$…） |
| 12 | window key（scrollX/locationbar/ondevicemotion） |
| 13 | performance.now() |
| 14 | Sentinel 内部 sid |
| 15 | URL search params |
| 16 | hardwareConcurrency |
| 17 | performance.timeOrigin |
| 18-24 | window feature flags |

被采样 JS 入口：accounts.google.com/gsi/client、
chatgpt.com/cdn-cgi/challenge-platform/scripts/jsd/api.js、
sentinel.openai.com/sentinel/{sv}/sdk.js。

### 4.2 四项工程细节的逐项审计

| 它的要求 | 我们 | 结论 |
|---|---|---|
| navigator.languages 单值 | runner 支持 --languages | ✅ |
| requestIdleCallback 默认 0 | 默认 0 | ✅ |
| NextAuth 不带 oai-client-* | get_nextauth_headers 已分离 | ✅ |
| 会话熔断 429→300s / 403→900s | 已实现同阈值 | ✅ |

### 4.3 启示

p[9]（随机/耗时）与 p[13]（performance.now）都是行为信号；
**纯协议难伪造的不是字段，而是字段背后连续交互产生的时间序列。**

---

## 5. hyhang915/gptfree-register

规模：54 个 py、2.97 万行。核心是 core/pipeline.py（266KB）+ chatgpt_register.py。

### 5.1 最有价值：错误码分类恢复

```python
if err_code == "registration_disallowed" and attempt < 2:
    continue                                   # 刷新 sentinel 重试
if err_code in {"invalid_auth_step", "user_already_exists", "username_already_exists"}:
    access_token, ... = await self._read_existing_session(auth)
    if access_token:
        return self._recovered_success(...)     # 恢复成成功
```

洞察：这些码往往意味着**账号其实已经建好**，判失败重来只会继续撞"邮箱已占用"。

### 5.2 已落地

`core/protocol_registration.py` 增加 _RECOVERABLE_CREATE_CODES 与恢复分支：
命中后 fetch_session() 读现有会话 → created={'recovered':True} → 继续落库；
恢复标记写入 extra.create_recovered。

回归测试 tests/test_protocol_recovery.py 4 项全 PASS，其中
`test_recovery_precedes_disallowed_retry` 断言恢复分支必须先于拒绝重试。

---

## 6. Regert888/gpt-auto-register

规模：24 个 py + 52 个 js。核心 auth_flow.py（179KB）+ sentinel_quickjs.py。

### 6.1 它文档里最重要的一句话

> 纯 Python 的 sentinel 算的是**合成 PoW**，能通过表面校验（200 OK），
> **但 OTP 派发服务端会用真 SDK JS 复验 token**，合成 token 过不了 →
> **邮件被静默丢弃**。

对我们意义：
- 我们的 sentinel_runner 跑的是**真实 sdk.js**（Node VM），属于"正确的一侧"；
- 我们协议注册能收到 OTP（日志 `取码 OK`），佐证 token 通过深层校验；
- 所以"收不到码"不是我们的主要问题，**存活**才是。

### 6.2 QuickJS 路线

它用 qjs 而非 Node 跑 sdk.js，需要 19KB 适配层桥接 requirements/solve。
两者都能跑真 SDK，没有优劣之分，本轮不迁移。

### 6.3 前端素材（留给前端轮次）

React 构建产物 webui/static/assets/index-Be9M5rEp.js（395KB）+ 多个 chunk，
另有 legacy_static_backup/app.js（48KB）。

---

## 7. 失败分类体系（2951461586 kernel 模块）

### 7.1 它的设计

```python
@dataclass(frozen=True)
class FailureClass:
    code: str
    markers: tuple[str, ...]
    attempt_retryable: bool = False
    retain_for_future_batch: bool = False
    batch_dropped: bool = False
    rotate_egress: bool = False
```

四个语义轴彼此独立；rotate_egress（换出口合理）**不等于**"是出口问题"。
FAILURE_CLASSES 的元组顺序即匹配优先序。标记字符串是线上数据，逐字保留。

### 7.2 已落地（core/failure_registry.py，9 类）

| 类别 | 示例标记 | 可重试 | 掉号 | 换出口 |
|---|---|---|---|---|
| cancelled | registration_cancelled | - | - | - |
| internal | nameerror / typeerror | - | ✅ | - |
| configuration | unsupported_registration_driver | - | ✅ | - |
| account | account_deactivated / token_revoked | - | ✅ | - |
| mailbox | remailerror / 收件箱为空 | - | - | ❌ |
| rate_limit | http-429 / too many requests | ✅ | - | ✅ |
| edge_challenge | cloudflare challenge / cf-chl | ✅ | - | ✅ |
| network | sslerror / curl: (35) | ✅ | - | ✅ |
| auth_state | invalid_auth_step / email_submit_stalled | ✅ | - | - |

接入：协议注册返回 `outcome` 字段；作业日志打印 `[Job N] 结果分类：xxx`。

测试 tests/test_failure_registry.py 8 项全 PASS
（含 test_mailbox_not_egress：邮箱侧失败换出口无意义）。

---

## 8. AuuCoder/gptGrok2api

规模：342 个 py 文件、11.3 万行，多平台（OpenAI + Grok）。最大模块：
`services/register_service.py` 190KB、`services/openai_backend_api.py` 151KB、
`services/register/openai_register.py` 142KB。

### 8.1 独立的 captcha-solver 体系

它有 9 个 solver 目录，每个都是"输入 URL/proxy → 输出可用凭证"的独立服务：

```
awswaf / botguard / cloudflare / datadome / hcaptcha /
perimeterx / recaptcha / turnstile / xai_browser
```

### 8.2 cloudflare solver 的做法（本轮核心借鉴）

README 写得很清楚：**它不伪造 token，而是把 full-page interstitial 真过一遍**：

- 导航到真实 URL，**轮询 cookie jar 直到 `cf_clearance` 出现且挑战标记消失**；
- Managed Challenge：best-effort 点击 `challenges.cloudflare.com` iframe 里的 Turnstile；
- JS Challenge：自动通过，无需点击；
- 统一返回 `solved: bool`，不按类型分支。

### 8.3 我们的问题与修复

我们此前 `_raise_if_cloudflare_challenge()` **一见标记就抛异常**：

```
看到 "Just a moment" → 立刻 raise CloudflareChallengeError
→ 上层换出口重跑整轮
```

而真实浏览器往往再等 5 秒就自己过了。等于把"等一等就好"误判成"出口坏了"。

**已落地** `core/cf_clearance.py`：
- `looks_like_challenge(state)`：按 url/title/text/errors 匹配 6 个标记
- `click_turnstile_if_present(driver)`：定位 iframe → 取中心坐标 → CDP 派发 mousePressed/mouseReleased
- `wait_for_clearance(driver, state_getter, deadline_s, poll_s, try_click)`：
  轮询直到挑战消失或超时，返回最后一次状态（不抛异常）

接入：`roxy_registration._raise_if_cloudflare_challenge` 改为**先等待再判定**，
配置 `CF_CLEARANCE_WAIT_SECONDS`（默认 45）、`CF_CLEARANCE_TRY_CLICK`（默认 True）。

测试 `tests/test_cf_clearance.py` **4 项全部 PASS**
（立即通过 / 等待后通过 / 超时放弃 / 标记识别）。

### 8.4 profile 占用回收（来自 2951461586）

浏览器持久 profile 的经典坑：父进程被杀后浏览器仍占着目录，
下一次启动报 `profile is already in use`，且不告诉你是谁占着。

它的两条判断：**看 lock 文件没用**（parent.lock 正常退出也会留），
唯一可移植答案是**找命令行里引用了该目录的活进程**。

**已落地** `core/profile_reclaim.py` + `tools/reclaim_profiles.py`：
- `find_holders(profile)`：扫 /proc/*/cmdline 匹配路径
- `reclaim(profile, terminate)`：终止仅限"看起来像浏览器"的进程（镜像名保护）
- `reclaim_stale(root, terminate)`：整目录扫描

实测：24 个 profile，全部空闲（0 被占用）。

---

## 9. 其余项目

（后续轮次补齐：sky3100、ZRainbow1275、basketikun、pirder、HengXin666、
starmiaoa、xiaoguzuiniu、pixian5、fuckoai、88cjohn、OpenBrowser、
upstream-ratio-watch、taiguanren、MAACodex、GPT_zhuceji、klsf、wenfxl、GuJumpgate）