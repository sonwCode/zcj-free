# turb-gpt-register 新会话完整交接

## 1. 交接目的

本文件用于新会话接手当前项目。内容分为已验证事实、历史改动、线上现状、未完成事项和接手流程。新会话不要根据历史对话中的“已经部署”直接推断当前线上版本，必须重新连接当前主服务器并核对。

## 2. 本地工作区

- 本地项目：`/home/hatch/dsh-harness/home/ds/turb-gpt-register-latest`
- Git 分支：`deploy/all-local-changes`
- 当时状态：本地分支比 `origin/deploy/all-local-changes` 超前 1 个提交
- 未提交文件：本交接文档本身 `SESSION_HANDOFF.md`
- 工作区原有代码未发现待处理的普通修改；新会话仍应重新执行：

```bash
git status --short --branch
git log --oneline -12
git diff --stat
git diff --cached --stat
```

## 3. 最近本地提交

- `819af58`：注册流程对所有 `/auth/login` 查询参数扩展 debounce。
- `559e6c0`：重发验证码时忽略重复的注册邮箱 OTP。
- `39bcb0c`：恢复参考项目的短信渠道选择行为。
- `c01e048`：停止 Cloak 元素句柄的递归展开。
- `75518cc`：保留 Cloak 脚本执行返回的嵌套 DOM 句柄。
- `84ca572`：使注册密码处理与参考流程一致。
- `ac9a922`：恢复参考 Codex 认证流程，同时保留密码传递。
- `fd21f1f`：使用原生 CloakElement 交互，去除 page.evaluate 包装。

这些是本地参考代码的真实提交，不等同于旧远端 `cuevm` 的部署提交。

## 4. 用户提出过的主要需求

### Codex 列表

- 在 Codex 授权页增加“手机信息”列。
- 手机号、归属地、价格合并在同一列显示。
- 该功能涉及后端记录投影、前端表头、数据行渲染和空列表 colspan。
- 曾出现 `/api/codex` HTTP 500，原因是 `core/db.py` 的 `_codex_content_to_record()` 中错误使用了不存在的 `payload` 变量，后改为 `content`。

### 列设置

- 曾尝试给“账号”和“Codex 授权”增加类似 sub2api 的列设置。
- 用户认为界面难看且曾导致前端数据/导航异常。
- 该功能已回滚，当前原则是不要重新添加列设置。

### Codex 邮箱 OTP

- OpenAI 邮箱验证码有效期约 30 秒。
- 旧流程从收到验证码到提交约 38 到 42 秒，容易过期。
- 历史优化包括减少输入后等待、提交后等待从 45 秒降到 15 秒、验证码等待从 90 秒降到 60 秒、重试次数从 3 增到 5。
- 新会话部署前必须确认这些优化是否已包含在当前主服务器代码，不能只依据旧提交号。

### Codex 补跑驱动选择器

- 曾添加过“默认/Cloak/Roxy/Browser Use/Protocol”选择器。
- 用户多次反馈样式不一致、换行、宽度错误，最终明确要求删除。
- 最终状态：前端驱动选择器已删除，`getCodexRetryDriver()` 已删除，请求中的 `driver` 参数也应删除。
- 补跑统一由配置控制，当前用户要求使用 `cloak`。

## 5. 旧云端曾做过的部署

历史远程别名：`cuevm`。

旧项目目录：`/opt/turb-gpt-register`。

旧服务：`turb-gpt-register.service`。

历史上曾部署过的改动包括：

- Codex 手机信息列及对应数据库投影。
- Codex OTP 延迟和重试优化。
- `CODEX_OAUTH_DRIVER` 从错误的多值字符串改为单值 `cloak`。
- 删除补跑浏览器驱动选择器。
- 恢复补跑调用链，使其调用：

```python
run_codex_oauth(email, force=True)
```

- CPA 管理接口 TLS 瞬时错误重试。

旧云端相关提交链：

- `57ec478`：曾添加补跑时选择驱动。
- `be23830`：修复 `getCodexRetryDriver` 未定义。
- `d64787c`、`1fbaa22`、`37cf2d8`、`7ddc8d8`、`73c2b82`：驱动选择器样式和布局反复修正。
- `16f01bf`：删除驱动选择器。
- `a0df655`：移除 `oauth_driver` 参数，恢复参考调用形式。
- `891800f`：恢复 Codex 补跑调用链。
- `ff3465f`：接入 CPA TLS 瞬时失败重试。
- `7f3b638`：把 CPA 重试参数加入配置覆盖列表。

注意：以上提交发生在旧远程服务器工作树，不要认为它们已经存在于当前本地主分支，也不要认为新主服务器已包含它们。

## 6. CPA TLS 问题及最终诊断

曾遇到：

```
BoringSSL SSL_connect: Connection closed abruptly
SSL_ERROR_SYSCALL
```

诊断结果：

- DNS 解析到 CPA 的 IPv4 地址后，TCP 443 可以建立连接。
- TLS ClientHello 发出后连接被关闭。
- IPv6 没有可用解析。
- Cloak 代理只影响 OpenAI 浏览器访问，CPA 管理接口请求通常是服务器自身直连。
- 旧代码虽有 `CPA_REQUEST_RETRIES=5` 和 `CPA_REQUEST_RETRY_DELAY=5`，但没有把它们接入实际的 `_cpa_request_json()`。
- 后来在旧云端接入了重试包装，捕获 SSL、连接重置、超时、502/503 等瞬时错误。
- 重试日志示例：`管理接口瞬时失败，重试 2/5`，之后曾成功获取授权地址。

当前新会话接手时必须核对：

```bash
rg -n "CPA_REQUEST_RETRIES|CPA_REQUEST_RETRY_DELAY|_cpa_request_json_once|_is_cpa_transient_error" core config
```

## 7. OpenAI 授权页连接问题

CPA 成功获取授权地址后，曾出现：

- `Page.goto: net::ERR_CONNECTION_RESET`
- `Page.goto: net::ERR_EMPTY_RESPONSE`

这说明失败点已经从 CPA 转移到：

`CloakBrowser 代理出口 -> auth.openai.com`

日志中 `api.ipify.org` 或 `checkip.amazonaws.com` 返回 200，只能证明代理可访问检测站，不能证明能稳定访问 OpenAI。

用户随后生成了 IPRoyal 美国代理，典型格式：

`http://client-...@proxy.iproyal.net:9000`

选择建议：

- 国家：美国
- IP 模式：粘性·不断开
- LIFE：60 或 120 分钟
- SESSION：固定
- 输出：服务器:端口:用户名:密码

筛选代理时应先测试：

```bash
curl -x "http://用户名:密码@服务器:端口" \
  --http1.1 -I --connect-timeout 15 --max-time 30 \
  https://auth.openai.com/
```

连续测试返回 HTTP 响应后，再用于 Codex。`ERR_EMPTY_RESPONSE`、`ERR_CONNECTION_RESET`、`SSL_ERROR_SYSCALL` 和超时都表示该代理不适合当前授权流程。

## 8. 线上迁移现状

这是当前最重要的部署边界：

- 用户说明“注册即服务迁移回主服务器”。
- 通过公网检查确认：`https://register.feixueapi.xyz/` 返回 HTTP 302 到 `/login?next=/`。
- 公网响应头显示经过 Cloudflare：`server: cloudflare`，`cf-cache-status: DYNAMIC`。
- DNS 查询显示公网域名被 Cloudflare 代理，不能直接看到真实源站。
- 旧远程别名 `cuevm` 对应的主机名曾是 `ab1d4949fd25`。
- 核查时旧服务器上的 `turb-gpt-register.service` 为 `inactive`，`MainPID=0`。
- 因此已确认：公网入口已经不再由旧 cuevm 上的运行进程提供。
- 目前尚未确认迁回后的主服务器 SSH 地址、SSH 别名、项目路径、systemd 服务名和源站代理配置。

**禁止默认操作 `cuevm`。** 新会话第一步必须向用户确认或从已知部署资料中确定主服务器连接信息，然后再检查：

```bash
hostname
systemctl status turb-gpt-register.service
systemctl show turb-gpt-register.service -p MainPID -p FragmentPath -p WorkingDirectory
ps aux | grep -E 'web.py|turb-gpt-register'
ss -ltnp
```

并确认源站实际响应，而不是只检查 Cloudflare 入口。

## 9. 当前未完成事项

1. 确认迁回后的主服务器 SSH 连接信息。
2. 在主服务器上确认实际部署版本和 Git 提交。
3. 对比主服务器与本地 `turb-gpt-register-latest` 的差异。
4. 确认 CPA 重试逻辑是否已迁移。
5. 确认 `CODEX_OAUTH_DRIVER=cloak` 是否是实际运行值，而非只存在于某个旧配置文件。
6. 确认补跑前端没有驱动选择器和残留请求参数。
7. 用一个测试账号验证完整链路：CPA 授权地址、OpenAI 页面、邮箱 OTP、手机验证、callback、凭证保存。
8. 验证代理出口对 `auth.openai.com` 的稳定性。
9. 测试通过后再考虑提交或推送本地分支领先的 1 个提交。

## 10. 推荐接手流程

### 阶段 A：识别实际线上主机

- 不修改文件。
- 确认当前 SSH 别名或地址。
- 检查主机名、服务状态、工作目录、监听端口、systemd 配置。
- 检查 Cloudflare 源站配置或服务器访问日志，证明公网请求落在哪台主机。

### 阶段 B：只读对比

- 读取主服务器 Git 状态和最近提交。
- 读取主服务器相关文件：
  - `core/codex_oauth.py`
  - `core/codex_retry_service.py`
  - `config/codex.py`
  - `webui/app.py`
  - `webui/templates/index.html`
  - `core/db.py`
- 与本地当前分支和参考仓库逐项对比。

### 阶段 C：小范围修复

- 先备份主服务器当前文件。
- 每次只改一个逻辑点。
- 修改后执行 `python3 -m py_compile`。
- 检查 `git diff --check`。
- 重启前记录服务状态。
- 重启后核对进程、日志和运行时配置。

### 阶段 D：验证

- 先测试 CPA 管理接口连通性。
- 再测试代理到 `auth.openai.com`。
- 最后执行一个单账号 Codex 补跑。
- 记录准确时间、代理出口、CPA 重试次数、页面导航结果和最终 callback 状态。

## 11. 重要判断

- `CODEX_OAUTH_DRIVER=cloak` 已在旧云端日志中生效，但新主服务器尚未重新确认。
- CPA 重试逻辑曾在旧云端生效，但新主服务器尚未重新确认。
- OpenAI 页面失败与 CPA 失败是两个独立问题：CPA 是服务器直连 TLS；OpenAI 页面是浏览器代理出口。
- 用户已经明确不需要补跑驱动选择器，不要重新加入该 UI 或参数。
- 用户希望前端尽量保持原本样式，不要再次做列设置或大范围前端重构。
- 不要把浏览器硬刷新、Cloudflare 缓存等当作服务端部署证明。
- 任何“已部署”结论都必须由远端文件回读、服务重启状态和实际日志共同证明。

## 12. 新会话第一条行动建议

先问或确认：迁回主服务器的 SSH 别名/地址是什么？如果已有别名，执行只读检查并把以下结果记录下来：

```bash
hostname
pwd
systemctl status turb-gpt-register.service --no-pager
systemctl show turb-gpt-register.service -p MainPID -p FragmentPath -p WorkingDirectory
cd /实际项目目录 && git status --short --branch && git rev-parse --short HEAD
```

确认这些结果后，才能继续部署或修复。

## 13. 2026-10-06 Codex 手机链修复部署记录

- 本地已验证：手机号/短信聚焦测试 54 项通过；Codex 短信结果测试 2 项通过；Roxy 邮箱 OTP 测试 5 项通过；关键 Python 文件 py_compile 通过。
- 本次修复包含：SMS 通道选择后回读确认、每个激活只提交一次、失败立即释放、收码后使用 status=6、收码前使用 status=8、释放瞬时错误同步重试、有序 SMSBower/Tiger provider 链、注册结果补写 phone_activation、协议驱动主流程调度修复和 OTP hook 前置。
- 生产主机：SSH 别名 `main-server`，主机名 `ser427180673937`，项目目录 `/opt/turb-gpt-register`。
- 同步前备份：`/opt/turb-gpt-register/deploy-backups/20261006T050407Z-codex-phone-sms/source-files.tar.gz`，包含 10 个被同步源文件；`turb.sqlite3`、`backups/`、`sentinel/` 未修改。
- 生产同步后 10 个目标文件与本地 SHA-256 全部一致；生产虚拟环境编译退出码为 0。
- 服务已重启：`turb-gpt-register.service` active，重启后 MainPID=356002；`curl -I http://127.0.0.1:5100/` 返回 HTTP 302 到 `/login?next=/`。
- 生产有效配置：`SMS_PROVIDER=smsbower`、`SMS_PROVIDER_CHAIN=`（空值表示按首选平台追加备用顺序）、SMSBower 已配置、Tiger 未配置；运行时解析链为 `smsbower,tiger`，实际尝试链为 `smsbower`。
- 重启期间旧 Playwright 子进程出现一次 EPIPE，systemd 随即正常停止旧进程并启动新进程；新进程启动日志和 HTTP 健康检查正常。
- 尚未执行真实账号的生产短信采购/补跑，因此 status=8/status=6 和实际 SMS 通道切换仍需下一次真实业务请求日志验证；本次部署本身没有产生新的短信费用。

## 14. 2026-10-06 第二轮短信边界修复

- 新增并通过 81 项相关回归测试：provider 配置/余额错误不跨平台 fallback、库存错误顺序 fallback、终态释放错误不重复请求、瞬时 HTTP 失败重试、FX 日志方向、自动注册嵌套 phone_activation 持久化。
- FX 日志已统一为 `rate_usd_cny` 和 `fx_source`；回退汇率输出稳定为 `7.2` 等短小数，不再出现 `7.199999...` 长尾。
- 生产第二轮备份：`/opt/turb-gpt-register/deploy-backups/20261006T053438Z-sms-boundary-fix/sms_provider.py.tar.gz`。
- 第二轮同步文件：`/opt/turb-gpt-register/core/sms_provider.py`，本地与生产 SHA-256 均为 `5cd6259280a56580599e27419f9cba415a215c0f0c227438874af25edb3d06b0`；生产 py_compile 退出码为 0。
- 第二轮重启后：`turb-gpt-register.service` active，MainPID=368920；WebUI 启动于 05:35:26 UTC；本地 HTTP 健康检查仍返回 302 `/login?next=/`。
- 第二轮重启后的生产日志没有真实短信采购事件，只有 WebUI 启动/健康请求；status=8/status=6、实际 SMS 通道保持和 provider fallback 仍由本地 81 项行为测试覆盖，待下一次真实 Codex 补跑产生业务日志后再观察。
