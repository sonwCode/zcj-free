# Agent Handoff: CPA Codex Upload Fix

生成时间：2026-10-07T03:26:58Z

## 1. 交接结论

本次任务的核心结果是：CPA 上传入口现在只接受 Codex 凭证，上传文件名使用 CPA 可识别的 Codex 命名，完整 JSON 字段保持原样。

代码实际位于独立仓库 zcj，不是生产注册项目 turb-gpt-register-latest。由于两个仓库的远程不同，本交接包把 CPA 代码差异导出为可复现补丁，主生产仓库只提交本交接材料，避免把独立项目源码误推到 zcj-free.git。

重要边界：

- 主生产仓库远程：https://github.com/sonwCode/zcj-free.git。
- CPA 上传源码仓库远程：https://github.com/sonwCode/zcj.git。
- 本轮没有把 zcj 的代码推送到 zcj-free.git。
- CPA 服务端已经存在的“其他”条目需要在 CPA 管理器中单独处理；本补丁只约束后续上传请求。
- 私钥、WebUI 授权码、CPA Management Key、邮箱凭证和数据库文件均不进入 Git。

## 2. 本地仓库状态

### 2.1 生产注册仓库

路径：/home/hatch/dsh-harness/home/ds/turb-gpt-register-latest

- 分支：deploy/all-local-changes
- 远程：origin https://github.com/sonwCode/zcj-free.git
- 交接开始前状态：工作区干净，HEAD 为 cd1cce9。
- 本轮新增文件：本交接文档、handoff/ 下的补丁和连接模板。

提交后用以下命令确认：

~~~bash
cd /home/hatch/dsh-harness/home/ds/turb-gpt-register-latest
git status --short --branch
git log -5 --oneline
git show --stat --oneline HEAD
~~~

### 2.2 CPA 上传源码仓库

路径：/home/hatch/dsh-harness/home/ds/zcj

- 分支：main
- 远程：origin https://github.com/sonwCode/zcj.git
- 应用前基线：d379027。
- 本轮本地提交：90ee7dd fix: restrict CPA uploads to Codex credentials。
- 提交后状态：相对 origin/main 已领先 4 个提交，工作区干净。
- 本轮提交文件：
  - application/tasks.py
  - platforms/chatgpt/cpa_upload.py
  - tests/test_cpa_upload.py
  - tests/test_platform_action_task.py
- 完整差异仍保存为 handoff/zcj-cpa-upload-fix.patch，供另一 agent 在不同工作树中复现。

应用补丁：

~~~bash
cd /home/hatch/dsh-harness/home/ds/zcj
git status --short --branch
git apply /home/hatch/dsh-harness/home/ds/turb-gpt-register-latest/handoff/zcj-cpa-upload-fix.patch
~~~

如果补丁已经应用过，先用 git apply --check 或检查四个文件的 diff，不要重复应用。

## 3. CPA 修复内容

### 3.1 上传边界

文件：platforms/chatgpt/cpa_upload.py

- 新增 Codex 文件名生成：codex-{email}-{plan}.json。
- 套餐读取顺序：plan_type，其次 chatgpt_plan_type，缺省为 free。
- 强制校验 payload 的 type=codex。
- 非 Codex payload 在 HTTP POST 之前返回：只允许上传 type=codex 的凭证。
- 复制完整 payload，仅把 type 规范为 codex，不会删除 workspace、token、profile 或其他有效字段。
- 保留原有 Management Key 请求头、URL 规范化、TLS 和请求超时逻辑。

### 3.2 旧 workspace 文件兼容

文件：application/tasks.py

workspace JSON 先通过现有 assert_workspace_cpa_json() 校验；校验通过后，对历史缺失 type 的可信 workspace 文件补齐 type=codex，再统一交给上传边界。

### 3.3 调用链

当前三条上传调用都会汇聚到 upload_to_cpa()：

- 注册后的自动上传：application/tasks.py::_auto_upload_cpa。
- workspace 刷新同步：core/lifecycle.py::refresh_and_sync_cpa。
- 手动平台动作：platforms/chatgpt/plugin.py::upload_cpa。

## 4. 验证结果

使用仓库虚拟环境：/home/hatch/dsh-harness/home/ds/.venv-zcj/bin/python。

已通过：

~~~text
/home/hatch/dsh-harness/home/ds/.venv-zcj/bin/pytest -q tests/test_cpa_upload.py
6 passed

/home/hatch/dsh-harness/home/ds/.venv-zcj/bin/pytest -q tests/test_platform_action_task.py -k 'cpa'
3 passed

/home/hatch/dsh-harness/home/ds/.venv-zcj/bin/pytest -q tests/test_api_accounts.py tests/test_platform_action_task.py tests/test_chatgpt_token_lifecycle.py
98 passed, 1 warning

/home/hatch/dsh-harness/home/ds/.venv-zcj/bin/python -m py_compile platforms/chatgpt/cpa_upload.py tests/test_cpa_upload.py
通过

git diff --check
通过
~~~

最终新增回归覆盖：

- URL 文件名为 codex-probe@example.com-free.json。
- POST JSON 保留额外 workspace 字段。
- type=other 不会触发 HTTP POST。
- 历史 workspace JSON 会补齐 type=codex。

## 5. 服务器连接资料

### 5.1 已验证的本机 SSH 解析

ssh -G main-server 得到：

- Alias：main-server
- HostName：64.83.20.57
- User：harness-root
- Port：22
- IdentityFile：本机已配置，实际路径未写入仓库。

连接模板：handoff/main-server.ssh.example。
环境参数模板：handoff/main-server.env.example。

安装模板：

~~~bash
mkdir -p ~/.ssh/config.d
cp handoff/main-server.ssh.example ~/.ssh/config.d/main-server
chmod 600 ~/.ssh/config.d/main-server
# 如果 ~/.ssh/config 没有 Include，手动追加：
printf '\nInclude ~/.ssh/config.d/main-server\n' >> ~/.ssh/config
ssh -G main-server | grep -E '^(hostname|user|port|identityfile) '
ssh main-server 'hostname && pwd'
~~~

私钥需要由服务器管理员通过原有安全渠道提供并放到模板 IdentityFile 指定的位置。

### 5.2 生产服务信息

- 项目目录：/opt/turb-gpt-register。
- systemd：turb-gpt-register.service。
- 内部监听：127.0.0.1:5100。
- 公网入口：https://register.feixueapi.xyz。
- 服务器上的运行时配置：/opt/turb-gpt-register/.env。
- 连接成功后的首轮只读检查：

~~~bash
ssh main-server 'hostname; pwd; sudo -n systemctl status turb-gpt-register.service --no-pager -l; sudo -n systemctl show turb-gpt-register.service -p MainPID -p FragmentPath -p WorkingDirectory'
ssh main-server 'curl --compressed -sS -I http://127.0.0.1:5100/'
ssh main-server 'cd /opt/turb-gpt-register && git -c safe.directory=/opt/turb-gpt-register status --short --branch && git -c safe.directory=/opt/turb-gpt-register rev-parse --short HEAD'
~~~

生产现场只读核对结果（2026-10-07）：

- hostname：ser427180673937。
- SSH 用户：harness-root。
- 服务：active。
- 内部 HTTP：302。
- 服务器工作树分支：master。
- 服务器工作树 HEAD：36c1413。
- 服务器 origin：https://github.com/sonwCode/zcj-free.git。
- 工作树已有 11 个已修改源文件，变更摘要为 1518 insertions / 469 deletions：config/codex.py、core/browser_use_codex_oauth.py、core/cloakbrowser_driver.py、core/codex_oauth.py、core/db.py、core/registration_service.py、core/roxy_codex_oauth.py、core/sms_provider.py、webui/config_editor.py、webui/templates/index.html、webui/templates/index_legacy.html。
- 工作树还有未跟踪目录：backups/、deploy-backups/、sentinel/。
- 以上是接手前已存在的生产现场；本轮没有修改、清理、提交或覆盖它们。

历史文档记录过多次 active 和 MainPID，但这些是历史证据。接手 agent 必须重新执行上面的实时检查；处理服务器 Git 前先备份并审阅 git diff，禁止直接 reset、clean 或全量 rsync。

## 6. 生产部署流程

当前 CPA 补丁不属于 turb-gpt-register-latest 源码，不能直接同步到 /opt/turb-gpt-register。如后续确认主服务器运行的是 zcj 项目，必须先确认远端工作目录、服务名和目标 Git 远程，再部署 CPA 补丁。

### 6.1 turb-gpt-register 只读预检

~~~bash
ssh main-server 'hostname; sudo -n systemctl is-active turb-gpt-register.service; curl --compressed -sS -o /dev/null -w "HTTP=%{http_code}\n" http://127.0.0.1:5100/'
ssh main-server 'sudo -n systemctl show turb-gpt-register.service -p MainPID -p WorkingDirectory -p FragmentPath'
~~~

### 6.2 生产备份

在服务器上执行，目录名使用 UTC 时间和变更标识：

~~~bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/opt/turb-gpt-register/deploy-backups/$STAMP-cpa-upload
sudo mkdir -p "$BACKUP"
sudo tar -czf "$BACKUP/runtime-before.tar.gz" \
  -C /opt/turb-gpt-register .env turb.sqlite3 2>/dev/null || true
sudo cp -a /opt/turb-gpt-register/.env "$BACKUP/.env" 2>/dev/null || true
~~~

不得把生成的 backup、.env、turb.sqlite3 复制回 Git 仓库。

### 6.3 部署后验证

~~~bash
ssh main-server 'sudo -n systemctl restart turb-gpt-register.service && sleep 3 && sudo -n systemctl is-active turb-gpt-register.service'
ssh main-server 'sudo -n journalctl -u turb-gpt-register.service -n 100 --no-pager'
ssh main-server 'curl --compressed -sS -I http://127.0.0.1:5100/'
~~~

预期健康检查通常是 HTTP 302 到 /login?next=/，这只能证明 WebUI 响应，不代表 CPA 远程上传成功。CPA 验证还需要查看上传日志和 CPA 管理器中的 Codex 分类。

### 6.4 回滚

~~~bash
ssh main-server 'sudo -n systemctl stop turb-gpt-register.service'
# 根据备份中的文件清单恢复，不覆盖未知运行时数据。
ssh main-server 'sudo -n systemctl start turb-gpt-register.service && sudo -n systemctl is-active turb-gpt-register.service'
~~~

如果是 Git 部署，先在服务器记录 git rev-parse HEAD，回滚到明确提交后再重启；禁止用未核对的 git reset --hard 覆盖运行时数据。

## 7. Git 提交与交接顺序

主仓库提交交接材料：

~~~bash
cd /home/hatch/dsh-harness/home/ds/turb-gpt-register-latest
git add AGENT_HANDOFF_20261007.md handoff/
git diff --cached --check
git commit -m "docs: add complete agent handoff and server templates"
git push origin deploy/all-local-changes
~~~

CPA 仓库本轮本地提交已经完成：90ee7dd。推送前必须确认目标远程确实是 https://github.com/sonwCode/zcj.git：

~~~bash
cd /home/hatch/dsh-harness/home/ds/zcj
git status --short --branch
git show --stat --oneline 90ee7dd
git remote -v
# 只有确认目标就是 zcj.git 后，才执行：
git push origin main
~~~

本轮交接执行的安全选择是：主仓库文档和补丁可以推送到 zcj-free.git；zcj 源码不自动推到另一个远程，避免项目串库。

## 8. 接手 agent Checklist

- [ ] 读取本文件和 SESSION_HANDOFF.md。
- [ ] 执行两个仓库的 git status --short --branch。
- [ ] 确认 main-server SSH 别名解析和实际 hostname。
- [ ] 只读检查服务、项目目录、当前提交和端口。
- [ ] 确认 CPA 代码实际运行仓库，再应用 handoff/zcj-cpa-upload-fix.patch。
- [ ] 运行 CPA 定向测试和完整回归集。
- [ ] 生产部署前创建带时间戳备份。
- [ ] 重启后确认 systemd、日志、HTTP 和 CPA 分类。
- [ ] 将真实 CPA 端旧“其他”条目与新上传结果分开记录。
- [ ] 任何提交前确认远程 URL，避免把 zcj 源码推到 zcj-free.git。

## 9. 当前风险与未完成项

- 本轮已只读验证 main-server：hostname=ser427180673937、用户=harness-root、服务状态=active、内部 HTTP=302；未执行部署或重启，历史 MainPID 仍不作为当前状态。
- CPA 远程已有的“其他”文件没有删除动作，避免误删配置。
- zcj 本地提交 90ee7dd 已完成；仍需在确认目标远程后决定是否推送到 zcj.git。
- 生产 turb-gpt-register 当前没有本轮 CPA 上传源代码，因此不要把本补丁直接当作该服务已部署。
- 现有旧 DEPLOY.md 包含历史样例配置，交接 agent 以本文件的占位符和服务器实际 .env 为准。
