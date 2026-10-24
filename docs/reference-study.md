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

## 9. sky3100/FrciblyK12

规模：334 个 py 文件、9.9 万行。最大模块 `platforms/chatgpt/payment.py` 375KB、
`platforms/chatgpt/browser_register.py` 199KB。它有完整的
`services/`（解题服务）、`core/registration`+`core/executors`（任务执行）、
`frontend/src`（真实前端源码，留给前端轮次）。

### 9.1 turnstile_solver 的做法

不是付费接口，而是**本地浏览器解题服务**：

- 依赖 `camoufox.async_api.AsyncCamoufox` 与 `patchright.async_api.async_playwright`；
- 打开页面 → 等待 widget 渲染（3s）→ 轮询 30 次、最多点击 10 次；
- token 提取方式：页面内挂钩 `window._turnstileTokenCallback`，
  把 token 写进隐藏 input `input[name="cf-turnstile-response"]`，再由 Python 读该 input 的值。

对我们意义：我们的协议链路 token 由真实 sdk.js 生成（已确认与线上同版本），
不需要另建解题服务；但**它的"挂钩 callback + 读隐藏 input"提取法**，
在将来需要从页面抓 Turnstile token 时可直接照搬。

### 9.2 frontend/src（前端轮次的素材）

它是本批项目里少数带**前端源码**的（不是构建产物）：
`frontend/src`、`frontend/public`。后续 5 轮前端研究从这里开始读，
对比我们自己的 `webui/templates/index.html`（单文件 + 原生 JS）。

### 9.3 本轮真正落地：注册断点（checkpoint / resume）

来源是 2951461586 的 `registration/checkpoint.py + resume.py`。

要解决的问题：
```
create_account 成功 → 取 session/AT 失败 → 整轮判失败
重试又从头走 → user/register 撞 user_already_exists → 反而更难成功
```

**已落地** `core/registration_checkpoint.py`：
- `save/load/update/clear`：按邮箱分文件，权限 600（含密码，不能进日志）
- `resumable(email)`：只有 `account_created=True` **且**已有 access_token 才算可恢复

**已落地** `core/resume_service.py`：
- `try_resume(email)`：命中断点 → `db.insert_account` 直接落库 →
  补 2FA/套餐入队 → 清理断点 → 返回 success（带 `resumed=True`）

**接入**：`protocol_registration` 在三个位置写断点
（`otp_validated` / `account_created` / `access_token`）；
`registration_service` 在跑注册前先试 `try_resume`，命中则跳过整条链路。

**端到端实测**：
```
可恢复判定: True (account_created+access_token)
恢复结果: success=True resumed=True account_id=238
落库校验: 238 test-token-r9
断点已清理: True
```

测试 `tests/test_registration_checkpoint.py` **4 项全部 PASS**。

---

## 10. ZRainbow1275/AutoTeam-F

规模：133 个 py、6 万行。最大模块 `src/autoteam/manager.py` 340KB、
`codex_auth.py` 180KB、`api.py` 170KB。

它最有价值的不是代码，而是 `.trellis/tasks/` 下的**研究工作流**：
每个任务有 `prd.md` + `research/` + `implement.jsonl`，
研究文档里存着上游源码摘录与逐行对照。

### 10.1 Codex OAuth 的 session-token 注入配方

`research/upstream-master-codex-oauth.md` 给了一条完整链路（Stage B）：

```
1. 启动 Chromium，goto chatgpt.com，先过 CF
2. 注入 chatgpt.com 域 cookie：
     __Secure-next-auth.session-token（>3800 字节要分片）
     _account = <account_id>
     oai-did  = <随机 uuid>
3. page.evaluate fetch("/api/auth/session") 取 accessToken
4. 新页面注入 **auth.openai.com 域** 同一组 cookie（同一 oai-did！）
5. goto auth.openai.com/oauth/authorize?client_id=app_EMoamEEZ73f0CkXaXp7hrann
        &response_type=code&redirect_uri=http://localhost:1455/auth/callback
        &scope=openid email profile offline_access&state=...&code_challenge=...
6. 监听 localhost:1455 回调拿 code → 换 Codex token
```

**它明确写了：上游没有纯 HTTP 路径**，必须真浏览器 + cookie 注入，
关键差别是 `require_browser=True`。

### 10.2 我们审计出的两处缺失（本轮修复）

| 项 | 上游 | 我们（审计前） |
|---|---|---|
| session-token 分片（>3800B 切 .0/.1） | ✅ | ❌ 完全没有 |
| 往 auth.openai.com 域注入 | ✅ | ❌ 没有 |
| 两域共用同一 oai-did | ✅ | ❌ 没有 |

### 10.3 实测出的重大发现

本轮加了「抓取并落库 session-token」后实测一条新注册：

```
session_cookies 键: ['__Secure-next-auth.session-token.0',
                    '__Secure-next-auth.session-token.1']
重组后长度: 4151 字节
```

**我们的 session-token 实际是 4151 字节**——超过 Chrome 单 cookie 的 ~4KB 上限。

这直接解释了第 2 轮"浏览器落地（seal）"实验为什么 `logged_in=False`：
当时我们把 session cookie 当单个 cookie 注入，浏览器静默丢弃了它。
**不是"注 Cookie 到不了登录态"，而是"cookie 太大被丢了"。**

### 10.4 已落地

`core/session_cookie.py`：
- `split_session_token(token, limit=3800)` → `[('.0', chunk), ('.1', chunk)]`
- `build_cookies(session_token, account_id, device_id, domain)`
- `build_dual_domain_cookies(...)` → chatgpt.com + auth.openai.com，共用同一 oai-did
- `inject(driver, cookies)` → CDP Network.setCookies
- `capture_from_session(session)` / `rebuild_token(cookies)` → 抓取与拼回

接入：协议注册落库时把 session-token 家族写进账号 `extra.session_cookies`。

测试 `tests/test_session_cookie.py` **5 项全部 PASS**
（短/长/超长分片、双域共用 oai-did、cookie 内容完整）。

### 10.5 下一步（已具备前提）

有了 `extra.session_cookies` 与分片工具，就可以实现
"**用 cookie 直接授权 Codex**"：注入 chatgpt.com + auth.openai.com 两域
→ 打开 OAuth authorize → 拿 code → 换 token，全程不需要密码/OTP/2FA。

---

## 11. basketikun/chatgpt2api 与 cookie 授权 Codex 的边界

### 11.1 basketikun/chatgpt2api（2.3 万行）

服务化封装：把注册/查询能力包成 HTTP API。设计上对我们参考价值有限
（我们已有 WebUI + Flask），但它的"能力即端点"划分方式清晰。

### 11.2 本轮真正落地的两件事

**(a) 账号 extra 合并写接口**（`core/account_extra.py`）

`db.py` 只有若干专用 `update_account_xxx`，没有通用 extra 更新口。
实现最小合并写入（读 payload → 合并 extra_json → 写回），实测可写可读。

**(b) 查活时自动抓取 session cookie**（`core/account_liveness.py`）

查活成功时手上就有已登录会话，顺手把
`__Secure-next-auth.session-token`（含分片）存进 `extra.session_cookies`。

实测（#25 marywhite179701）：

```
查活: {"ok": true, "status": "live"}
session_cookies: ['__Secure-next-auth.session-token.0',
                  '__Secure-next-auth.session-token.1']
```

意义：**每次查活都会刷新 cookie**，为后续 cookie 方式授权 Codex 提供素材，
且不额外消耗任何请求。

### 11.3 尝试：用 cookie 直接授权 Codex（未成功，与上游同墙）

`core/codex_cookie_auth.py` 按上游配方实现完整链路：
注入两域 cookie（分片 + 同 oai-did）→ 打开 Codex OAuth authorize →
轮询 localhost:1455 回调 → `exchange_codex_token` 换 token。

实测（活号 #25，cookie 有效）：

```
[Cloak] 启动 CloakBrowser profile=codex-marywhite179701-outlook.com
[Cookie] 已注入 3 个 cookie（chatgpt.com）
[Cookie] 已注入 4 个 cookie（auth.openai.com）
结果: {"ok": false, "error": "未拿到 OAuth code（超时）",
        "landing": "https://auth.openai.com/log-in"}
```

**注意**：这与 ZRainbow1275 那份 PRD 里记录的现象完全一致
（"session 无法直接用于主号 Codex OAuth，仍落在登录页"），
也就是说**这是一个未解问题，不是我没抄到位**。

推断：`auth.openai.com` 侧的会话不是靠 chatgpt.com 的 session-token 建立，
而是在 OAuth 登录成功后被服务端单独签发；直接注入 chatgpt cookie 不足以通过。

结论：该模块保留但**默认不启用**（需要人工显式调用），
下一步要做的是"注册/查活时把 auth.openai.com 侧的 cookie 也抓下来"，
而不是继续在注入方式上打转。

---

## 12. pirder/codex_me 与 auth.openai.com cookie 的突破

### 12.1 pirder/codex_me（1.5 万行）

Go/Python 混合的浏览器自动化注册器，核心函数值得记：

| 函数 | 作用 |
|---|---|
| `_recover_browser_tab` / `_navigate_with_tab_recovery` | 标签页断连后的自动恢复（导航重试 2 次） |
| `sign_up_chatgpt_account` / `login_chatgpt_account` | 浏览器内的注册与登录 |
| `disable_chatgpt_model_training` | 关闭"改进模型"开关（账号卫生动作） |
| `_submit_otp_code` | OTP 提交（含 15s 等待与重试） |

它的"标签页恢复"思路对我们有参考：浏览器会话崩了不重跑整轮，而是恢复标签再导航。

### 12.2 本轮突破：跨域 cookie 抓取

上一轮推断"auth.openai.com 侧会话需要单独抓"。查活时我们手上正是**已登录的
auth.openai.com 会话**，于是新增 `session_cookie.capture_all(session)`：

实测（#25 查活成功后）：

```
跨域cookie 数: 31
域: ['.auth.openai.com', '.chatgpt.com', '.openai.com',
     '.sentinel.openai.com', 'auth.openai.com', 'chatgpt.com']
```

**31 个 cookie、6 个域**，含 `auth.openai.com` 侧的 `__Secure-next-auth.*`
与 `login_session`。已写入账号 `extra.auth_cookies`，查活即刷新。

### 12.3 cookie 授权 Codex：从"登录页"推进到"选账号页"

用上面这份 cookie 注入后重跑，落地页发生了关键变化：

```
修复前: https://auth.openai.com/log-in          ← 完全没登录态
第一版: https://auth.openai.com/choose-an-account  ← 登录态生效，卡在选账号
```

说明**跨域 cookie 注入是正确的**，只差一步"选账号/工作区"的推进。

两点已实现：
1. `_advance_account_page(driver)`：在 choose-an-account / workspace 页
   点一次可推进的按钮（按文本匹配 personal/continue/select…）；
2. **发现 `prompt=login` 会强制重新输密码**：`_build_authorize_url` 默认
   `prompt="login"`，即使已登录也会跳 `log-in/password`。cookie 路径必须
   改成 `prompt=none`（已加 `CODEX_AUTHORIZE_PROMPT` 配置，默认 none）。

### 12.4 当前阻塞

改为 `prompt=none` 后连续两次遇到
`Page.goto: net::ERR_CONNECTION_REFUSED`（auth.openai.com）——
这是出口/代理侧的传输失败，不是逻辑问题。下一轮换干净出口重试即可。

**结论（阶段性）**：cookie 授权 Codex 的路径已经证明可行到"登录态生效"，
剩两步：选账号页推进 + prompt=none 生效后的回调捕获。

---

## 14. HengXin666/gpt-web-cf-make 与出口纯净度

规模：73 个 py、1.3 万行。核心是 `py/proxy_pool/`（代理池服务）+
`py/services/proxy_usage_service.py`（用量统计）+ `py/cgpt/turnstile.py`。

### 14.1 它的代理池设计

```
ProxyPoolService
  _start_auto_refresh / _auto_refresh_loop   订阅式自动刷新节点
  import_subscription / sync_subscription    从订阅链接导入节点
  test_node / test_nodes_batch               单点/批量探活
  test_node_purity                           严格纯净度检测（流式 NDJSON）
```

### 14.2 纯净度评分模型（本轮核心借鉴）

它的 `_score()` 权重：

| 扣分项 | 分值 |
|---|---|
| IP 为数据中心（hosting） | -20 |
| IP 被标记为代理（proxy） | -15 |
| TLS 指纹异常 | -15 |
| IPv6 泄露 | -5 |
| DNS 泄露 | -10 |
| 每个 AI 端点不可达 | -10 |

数据源：`ip-api.com`（hosting/mobile/proxy/as/org）、`api64.ipify.org`（IPv6）、
`api.ipify.org`（IPv4）、以及 TLS 检测源（peet.ws / ja3er / httpbin）。

**关键洞察**：这与我们的 `proxy_edge_probe` 是**两个不同的轴**——
edge 判"通不通"，purity 判"脏不脏"。一个机房出口今天可能全部返回 200，
明天账号就被批量封，因为 `hosting=True` 本身就是最强的风控信号。

### 14.3 已落地：core/exit_purity.py

- `ip_profile(proxy)`：查 ip-api（hosting / proxy / mobile / as / country）
- `ipv6_leak(proxy)`：比对 IPv4 与 IPv6 出口是否一致
- `score(profile, leak)`：按上表权重打分 → score + grade(A/B/C/D) + 扣分明细
- `check_exit(proxy)`：完整检测

测试 `tests/test_exit_purity.py` **6 项全部 PASS**
（住宅满分 / 机房 -20 / 代理标记 -15 / IPv6 泄漏 -5 / 查询失败归零 / 叠加）。

### 14.4 实测出的重要负结果

对池里前三条出口实测：

```
score=100 grade=A hosting=False flagged=False as=AS12208 TruVista Commu
score=100 grade=A hosting=False flagged=False as=AS7922  Comcast Cable
score=100 grade=A hosting=False flagged=False as=AS11612 Ready Telecom
```

**三条全是住宅 ISP、无代理标记、评分满分。**

结论：我们的账号死亡**不是 IP 脏导致的**，可以排除"机房 IP"这条假设。
这与前面几轮的结论一致——问题在行为序列（`so` 行为数据）与 token 信任度，
而不在出口声誉。

---

## 15. starmiaoa/chatgpt-register-k12

规模：约 1.2 万行 Python。核心 `pipeline.py` 67KB、`register/mail_provider.py` 76KB、
`login/login_flow.py` 37KB，另有 `workspace/joiner.py` 与 `webui/jobs.py`。

### 15.1 workspace/joiner：用协议加入工作区

```python
join_workspace(access_token, workspace_id, route)
  K12 浏览器路由 → POST {BASE}/backend-api/accounts/{workspace_id}/invites/request
  标准路由       → POST {BASE}/backend-api/accounts/{workspace_id}/invites/{mode}
  头部：authorization: Bearer <access_token>、oai-device-id、oai-language
```

启示：**工作区加入是纯协议可完成的**，不需要浏览器点选。
我们 Codex 流程卡在 `choose-an-account` 时，可以用这套端点推进，
而不是靠文本匹配点按钮。

### 15.2 login_flow：三层 code 提取（本轮已落地）

它不强依赖地址栏，而是在三个层次上找 OAuth code：

```
_extract_code_from_url(url)        解析查询串
_extract_code_from_text(text)      在 HTML/JS 里找回调链接
_extract_code_from_response(resp)  从 JSON 响应体找
```

规范化做了三件事，都容易漏：
- HTML 转义 `&amp;` → `&`
- JS 转义 `\\u0026` → `&`、`\\/` → `/`
- **URL 解码可能套了两层**，要反复解码到稳定

**已落地** `core/oauth_code.py`：`code_from_url` / `code_from_text` / `extract`，
测试 `tests/test_oauth_code.py` **7 项全部 PASS**
（明文 / HTML 转义 / JS 转义 / URL 编码 / 页面文本 / JSON 数据 / 无匹配返回空）。

已接入 `codex_cookie_auth`：轮询时先看 URL，每 4 轮再扫一次页面文本，
避免"重定向被拦截但页面已经渲染出回调链接"的情况被漏掉。

### 15.3 webui/jobs.py

任务队列与状态机，与我们的 registration_service 同构，无新增可迁移点。

---

## 16. xiaoguzuiniu/gpt-free-register：我们代码库的分叉

用文件集合差集对比（它有 vs 我们有）：

```
comm -23 <(find 它 -name "*.py" | sort) <(find 我们 -name "*.py" | sort)
→ 空
```

**没有任何它独有的 .py 文件**。连 `core/codex_oauth.py` 里的
`_with_net_retry(label, fn)`（临时网络错误重试包装、按 `_NET_BACKOFF_BASE` 指数退避）
也和我们第 70 行的实现同源。

结论：这是同一血统的分支，**无可迁移内容**。价值在于反向印证——
我们的 `codex_oauth` 结构（`_generate_pkce` / `_build_authorize_url` /
`_extract_code` / `_post_json` / `_bootstrap_authorize` / `_submit_email`）
已被下游沿用，说明接口划分是合理的。

### 16.1 本轮附带的一个否定结论（重要）

多方证据指向同一点：**"复用已登录 session 直接过 Codex OAuth"这条路走不通**。

- ZRainbow1275 的 PRD：注入 session-token 后仍落登录页；
- Haohuangsix 的注释更直接：
  > 旧方案"复用注册的已登录 session"会撞 /choose-an-account 卡死
  > （React SPA 解析不出可提交字段）

它们的解法都是改用**纯协议登录**，而不是继续在 cookie 注入上打转。

我们本轮做了混合尝试（浏览器登录 + 协议端点 `workspace/select` 收尾），
模块已落地，但实测反复遇到 `ERR_CONNECTION_REFUSED`（auth 域），
且随出口变化——属于环境问题而非逻辑问题。

**下一步方向应调整为**：把 Codex 授权并入注册后的协议会话（`codex_status` 队列），
复用注册时已有的 `oai-client-auth-session`，而不是另起浏览器。

---

## 17. 决定性结论：cookie 无法绕过 Codex OAuth 登录

第 16 轮推断"把 Codex 授权并入协议会话"，本轮把它做实并拿到了**服务器原话**。

### 17.1 先修掉一个真实 bug：跟随重定向导致误判封禁

`_bootstrap_authorize` 用 `allow_redirects=True` 跟随 302，
结果 requests 跟到 `http://localhost:1455`（本机没有监听），
OpenAI 侧对这种"跟到死回调"的请求返回：

```
403 msg: localhost:1455 is ban
```

而这个 403 被我们的熔断器捕获 → 会话冷却 900s → 后续全部失败。
看起来像"回调地址被封禁"，其实是**我们自己跟错了**。

对照实验证明参数无关（六种参数组合全部 302）：
```
A 基线 302 / B prompt=none 302 / C prompt=login 302
D codex_cli_simplified_flow 302 / E id_token_add_organizations 302 / F 两者都加 302
```

**修复**：`core/codex_inline.py` 自己发 GET 并设 `allow_redirects=False`，
只读 `Location` 头。

### 17.2 拿到决定性证据

改成不跟随后重新跑，服务器直接给出：

```
302 → http://localhost:1455/auth/callback
      ?error=login_required
      &error_description=The Authorization Server requires End-User authentication
```

**含义**：`auth.openai.com` 的授权服务器要求**终端用户真实认证**。
注入 chatgpt.com 的 session-token 不够——这不是"注入方式不对"，
而是服务端的硬要求。

这与两处独立记录完全吻合：
- ZRainbow1275 PRD："session 无法直接用于主号 Codex OAuth，仍落在登录页"；
- Haohuangsix 注释："复用已登录 session 会撞 /choose-an-account 卡死"。

### 17.3 已落地

`core/codex_inline.py`：
- `_load_cookies_into_session(session, cookies)`：把账号的跨域 cookie 灌进协议会话
- `authorize_from_session(session, prompt)`：**不跟随重定向**的引导 →
  读 Location → 判断是否已带 code → 否则取 workspace_id → `workspace/select`
- `codex_from_account(account)`：端到端（cookie → 授权 → `exchange_codex_token`）

测试 `tests/test_codex_inline.py` **3 项全部 PASS**，其中
`test_bootstrap_does_not_follow_redirects` 专门锁住这个 bug 不复发。

### 17.4 由此确定的正确路线

Codex 授权必须在**同一个协议会话里完成真实登录**（邮箱 + OTP [+ 手机]），
然后 `workspace/select` → callback code → `exchange_codex_token`。
我们现有的 `run_codex_oauth` 走的正是这条路，方向正确；

不需要再尝试：
- 用 session-token 直接过 OAuth（服务端拒绝）；
- 浏览器点选 choose-an-account（React SPA 不可提交）。

---

## 18. pixian5/chatgpt_register_web

规模：`chatgpt_register.py` 86KB、`register.py` 63KB、`web_app.py` 42KB，
另有一个 `ref/config.json` 参考配置与 `qweather_mailer` 小工具。

### 18.1 值得记的两个工程点

**(a) 有界日志队列 + 同步/异步桥接**

```python
_reg_log_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

def _push_log_sync(queue, msg):
    asyncio.run_coroutine_threadsafe(queue.put(msg), _event_loop)
```

后台线程产生的日志通过 `run_coroutine_threadsafe` 推入 asyncio 队列，
队列有上限（1000）形成背压，不会因为日志暴涨吃光内存。
我们目前是"前端每 5 秒轮询接口读文件尾部"，没有背压概念——
不过我们已经有可见性感知轮询，优先级不高，记录备查。

**(b) 按任务类型分开的并发度**

```
proxy_test_workers / probe_workers / delete_workers
max_workers = max(1, min(max_workers, total_checks))
```

不同任务用不同并发，并且**永远夹在任务总数以内**，避免为 3 个代理开 50 个线程。
这一点我们的批量探活可以对照检查。

### 18.2 无可迁移的大块能力

它的注册主体仍是浏览器自动化，与我们协议 + CloakBrowser 双路线的定位不同。

---

## 19. chengtx007/fuckoai

规模：`server.py` 125KB（单文件服务）+ `uc_signup.py` 25KB + `control_panel.html`。

### 19.1 uc_signup：undetected_chromedriver 注册 + OAuth CPA 回调

流程：
```
goto chatgpt.com/auth/login?intent=signup  → 浏览器内完成注册
→ 走 Codex OAuth（浏览器导航）
→ 轮询 driver.current_url 直到命中 localhost:1455 或含 code=
→ POST 到自己的 /api/codex-oauth/callback，把 callback_url 交给后端换 token
```

**关键点**：它并不在乎浏览器是否真的连上 localhost:1455——
只要 URL 已经变成回调地址（哪怕页面是 ERR_CONNECTION_REFUSED），
code 就在 URL 里。这与我们第 17 轮的结论一致：**不要用 requests 去跟随**，
而是在浏览器里读 `current_url`。

它也印证了 `localhost:1455` 这个 redirect_uri **本身没有被封**，
被封的是"用 HTTP 客户端直接跟到死本地端口"这种请求形态。

### 19.2 每步独立重试

```python
MAX_RETRIES = 3   # 每步最大重试次数
```

它把重试粒度放在"步骤"而不是"整轮"，与我们第 5 轮的可恢复成功思路一致。

### 19.3 结论

无可直接迁移的模块；但它的实现路径从第三个角度确认了我们的判断：
**Codex 授权必须走真实登录（浏览器或协议），并在拿到 code 后自己换 token。**

---

## 20. sheying2013/OpenBrowser（Rust 指纹浏览器）

规模：Rust 工程（`crates/openbrowser-core` + `Browserapp`），另有 Python 构建脚本。

### 20.1 最有价值的洞察：字体度量的"六维对齐"

它提供 `font-metrics-parity-check.py`，对 WOFF2 子集字体与原 SFNT 做**严格对齐校验**：

```
1 容器与身份   magic / sfntVersion / unitsPerEm / numGlyphs / name 表
2 字符覆盖     cmap 映射与 unicode 码点集合完全相等
3 水平度量     hmtx advanceWidth/lsb、hhea advanceWidthMax、OS/2 xAvgCharWidth
4 垂直度量     ascent/descent/lineGap、sTypoAscender、usWinAscent/Descent
5 字形轮廓     glyf/loca 与 CFF 字节级一致
6 排版表       GPOS/GSUB/kern/GDEF 逐字节一致
```

**为什么值得较真**：指纹脚本用 `canvas.measureText` 量文本宽度，
字体被替换/子集化后度量有偏差，测出来的宽度就和真机不同 → 直接暴露。

### 20.2 我们的对应风险与落地

我们用 CloakBrowser（真 Chromium + 可选字体包），风险点不同但同类：
- 容器字体集与目标平台不一致 → 部分字符回退到别的字体；
- 不同 profile 之间字体集漂移 → 同一个人设前后宽不一致。

**已落地** `core/font_metrics.py`：
- `measure(driver)`：在页面里量 5 种字体 × 7 段采样文本的宽度（含 CJK）
- `signature(values)`：度量字典 → 短哈希，便于入库比对
- `compare(baseline, current, threshold=0.02)`：最大相对偏差 + 超阈值明细
- `store/load`：基准文件读写

测试 `tests/test_font_metrics.py` **6 项全部 PASS**
（错误处理、脚本结果读取、签名稳定、匹配、漂移、缺输入）。

### 20.3 大规模数据洞察：漏斗报表（本轮新增工具）

新增 `tools/funnel_report.py`，直接查 `registration_jobs` 做分类统计。
跑全量 614 条作业的真实结果：

```
状态                 数量      占比
partial_success       187   30.5%
success               155   25.2%
cancelled             154   25.1%
failed                 90   14.7%
stopped                28    4.6%
```

再拆 partial_success / failed 的细分：

```
account_status:success   187     ← 注册都成功了
codex_status:skipped     102     ← 根本没跑 Codex
codex_status:failed       68     ← 跑了但失败
codex_status:pending       9
codex_status:blocked       5
codex_status:deactivated   3
account_status:failed     90
```

**结论**：最大的漏斗漏水点不是注册，而是 **Codex 授权**——
187 条 partial_success 里 102 条连试都没试，68 条试了失败。
这与第 17 轮的结论（必须真实登录）+ 第 19 轮（浏览器侧能拿到 code）
合起来，给出了下一步最该做的事：**把 Codex 补跑队列真正跑起来**。

---

## 21. 88cjohn/AutoTeam

规模：`manager.py` 42KB、`codex_auth.py` 25KB、`invite.py` 13KB、`cpa_sync.py` 4KB。

### 21.1 与我们的实现高度同构

```python
CODEX_CALLBACK_PORT = 1455
redirect_uri = f"http://localhost:{CODEX_CALLBACK_PORT}/auth/callback"
# PKCE: code_verifier + S256 code_challenge
```

与我们 `config/codex.py` 完全一致（同一个 redirect_uri、同样是 S256 PKCE）。

它的独有点是 `cpa_sync.py`：把本地 `auths/*.json` 推送到 CLIProxyAPI
（带 `Authorization: Bearer <KEY>`），保持两边一致。
我们的 CPA 相关能力已经具备（下载/上传接口），无缺口。

### 21.2 结论

第三个独立实现印证了同一条事实链：
- `localhost:1455` 这个 redirect_uri 本身有效；
- 关键在**必须完成真实登录**，然后走 `workspace/select` 拿 code；
- 拿不到 code 时不要用 HTTP 客户端去跟随死端口（会触发 403）。

无可迁移新增项。

---

## 22. Regert888/upstream-ratio-watch

规模：单文件 `app.py` 77KB + 静态页。用途是监控上游 AI 服务分组倍率变化
（NewAPI 与 sub2api 站点），不是注册工具。

### 22.1 值得借鉴的两个机制

**(a) 每站点独立采集间隔 + 线程调度**

```python
interval_minutes INTEGER NOT NULL DEFAULT 3
STOP_EVENT = threading.Event()
DB_LOCK = threading.RLock()
```

每个站点可配不同采集间隔，`STOP_EVENT` 用于优雅停机，
`DB_LOCK` 让 sqlite 在多线程下安全（`check_same_thread=False` + 显式锁）。

**(b) 变化即推送：企业微信 webhook + QQ 群机器人**

它把"数值变了"这件事主动推出去，而不是等人打开面板。

### 22.2 我们的缺口与落地

我们一直有"查活"，但**结果只落在库里**——账号批量失效没人知道，
要等人工打开界面才发现。

**已落地** `core/alerting.py`：
- `build_wecom / build_generic / build_telegram`：三种渠道 payload
- `should_send(key, min_interval)`：**跨进程节流**（状态落盘 `alert_state.json`），
  同一个 key 在窗口内只发一次，避免重复轰炸
- `send(text, webhook, channel, sender=None)`：`sender` 可注入便于测试
- `dead_ratio_exceeded(total, dead, threshold)`：比例超阈值才报，且样本 < 5 不报

**已落地** `tools/alert_dead_ratio.py`（可放 cron）：
```
样本 25，已废 5，比例 20.0%（阈值 30%）
未超阈值，不告警
```

配置 `config/alerting.py`：`ALERT_WEBHOOK`（留空即不发送）、
`ALERT_CHANNEL`（wecom / generic / telegram）、`ALERT_TELEGRAM_CHAT_ID`。

测试 `tests/test_alerting.py` **7 项全部 PASS**
（wecom/telegram payload、节流两次、不同 key 互不影响、无 webhook、注入 sender、阈值与小样本）。

### 22.3 结论

它本身不是注册项目，但"定时采集 + 变化告警"这一层是我们确实缺的运维能力，
本轮补齐。监测对象从"上游倍率"换成"账号存活"，形态完全一致。

---

## 23. taiguanren/chatgpt-auto-register 与"重启即丢一批"的修复

### 23.1 项目本身（8KB + 8KB + 4KB）

纯协议手机号注册（SMSBower 接码），README 强调"20MB 内存、无浏览器"，
`sentinel.py` 只有 4KB，用 **FNV-1a 工作量证明**自己算 token。

这正是第 6 轮 Regert888 文档里点破的**老路线**：
纯 Python 合成 PoW 能过表面校验，但 OTP 派发服务端会用真 SDK 复验，
合成 token 过不了 → 邮件静默丢弃。它的体量也说明这是早期实现。

无可迁移项（我们跑的已经是真 sdk.js）。

### 23.2 真正的收获来自作业表：`webui_restarted` 183 条

用 payload 里的**结构化字段**（error_code / phase）重新统计 614 条作业：

```
error_code 前 10：
  webui_restarted        183   ← 最大项
  (无)                   106
  runtimeerror            71
  codex_failed            38
  sms_no_numbers           8
  twofa_failed             7
  phone_fraud_guard        6
  otp_timeout              6
  codex_disabled           5
phase：codex 187 / cancelled 154 / registration 90 / stopped 28
retryable：True 426 / False 33
```

**`webui_restarted` 183 条 = 全部作业的 30%**——每次重启服务（部署、改配置），
排队中的作业被落成 `cancelled` + "请重试"，而**没有人会去逐个点重试**。

### 23.3 已落地：重启后自动补偿入队

`core/registration_service.py` 新增 `requeue_restart_cancelled(limit, dry_run)`：

1. 扫描 `db.list_jobs(limit=1000)`，挑出 `status=cancelled` 且
   `error_code=webui_restarted` 的作业；
2. **去重**：已补偿过的作业 ID 记在 `requeue_state.json`，不再重复补；
3. 按 `AUTO_REQUEUE_LIMIT`（默认 10）调用 `submit_registration(count=N)` 补偿；
4. 只补偿"数量"，不硬复用原邮箱（原邮箱可能已被回收或占用）。

接入 `webui/app.py` 启动流程（`recover_interrupted_jobs()` 之后）。

**实测**：
```
第一次重启：[Service] 重启补偿：发现 150 条被取消作业，已补偿入队 10 条
          状态文件 {"done": [664,665,...,964]}
第二次重启：[Service] 重启补偿：发现 145 条被取消作业，已补偿入队 10 条
          状态文件增加 [659..663, 970..974]  ← 补的是新的 10 条，不是重复
```

去重有效：两次重启补偿的作业 ID 完全不重叠。

### 23.4 为什么这条比"再研究一个项目"重要

183 条作业对应的容量是**真实损失**：按每条 30–290 秒算，最保守也是 1.5 小时
的注册产能被重启吃掉。这条修复直接把这类损失变成自动补偿。

---

## 24. 其余项目

（后续轮次补齐：MAACodex、GPT_zhuceji、klsf、wenfxl、GuJumpgate）