# 前端专项研究笔记（对标参考项目，5 轮以上）

> 目标：研究参考项目的前端组织方式，把好的部分迁移到我们自己的 UI。
> 我们的现状：WebUI 是**单个** `webui/templates/index.html`。

## 第 1 轮：现状盘点与第一个落地

### 1.1 我们的前端实况（实测）

```
index.html     8031 行 / 428 KB
<style> 块     3 个（合计约 2500 行内联 CSS）
<script> 块    3 个
顶层函数       278 个
构建步骤       无（原生 JS，服务端直接吐模板）
```

**问题清单（按影响排序）**

| # | 问题 | 影响 |
|---|---|---|
| 1 | 单文件 428KB，每次首屏全量下载 | 首屏慢，移动端尤其明显 |
| 2 | 278 个函数平铺在同一作用域 | 命名冲突风险、难以定位、无法按需加载 |
| 3 | 表格/列表渲染逻辑重复（sms-pool、accounts、logs 各写一套） | 改一处漏三处 |
| 4 | 轮询在页面不可见时照常跑（本轮已修） | 无谓请求与日志写入 |
| 5 | 无组件边界，状态散落在全局变量（smsPoolState 等） | 状态来源不清晰 |

### 1.2 参考项目的组织方式

| 项目 | 形态 | 结构 |
|---|---|---|
| sky3100/FrciblyK12 | React + Vite | `frontend/src` 组件目录 + `package.json`（另有 electron 壳） |
| Regert888/gpt-auto-register | React（构建产物） | `webui/frontend` 源码 + `webui/static/assets/index-*.js` 产物 |
| AuuCoder/gptGrok2api | Vue | `web-vue/package.json` |

它们共同点：**组件化 + 构建步骤 + 静态资源分片加载**。
我们相反：单文件、无构建、全量内联。

### 1.3 本轮落地：可见性感知的轮询

5 个轮询函数（`pollLog` / `pollRetryLog` / `pollLiveLog` / `pollTotpLog` /
`pollEmailChangeLog`）全部加上守卫：

```js
if (document.visibilityState === 'hidden') return;
```

并新增 `visibilitychange` 监听：切回前台**立刻补拉一次**，
避免"切回来看到的还是旧日志"。

实测部署：文件 8031 行；守卫 6 处（含监听体）；服务 active；`/`、`/accounts` 均正常 302 到登录页。

### 1.4 后续 4 轮的迁移计划（不推翻现有实现，逐步分段）

| 轮次 | 目标 |
|---|---|
| 第 2 轮 | 抽出通用表格渲染器（accounts / sms-pool / logs 三处合一） |
| 第 3 轮 | 抽出状态容器（`store.js` 风格），消除散落全局变量 |
| 第 4 轮 | 静态资源拆分：CSS/JS 外链 + 缓存头，首屏只加载当前页所需 |
| 第 5 轮 | 交互细节对标（加载骨架屏、批量操作工具栏、键盘快捷键） |

原则：**不引入构建链**（部署是单机 systemd + scp，引 Node 构建会显著复杂化），
而是用"外链文件 + 小型模块约定"达到同等可维护性。
## 第 2 轮：分页逻辑合流（真删重复代码）

### 2.1 发现

`renderSmsPoolEligible` 与 `renderSmsPoolEvents` 各自写了一遍同样的算术：

```js
const pages = Math.max(1, Math.ceil(list.length / size));
page = Math.min(page, pages);
const start = (page - 1) * size;
const items = list.slice(start, start + size);
```

事件列表还多一份"夹住页码 + 计算汇总区间"的变体。

### 2.2 落地

新增通用分页器（放在工具函数区）：

```js
function paginate(list, page, size) {
  const items = Array.isArray(list) ? list : [];
  const per = Math.max(1, Math.floor(Number(size) || 20));
  const pages = Math.max(1, Math.ceil(items.length / per));
  const current = Math.min(Math.max(1, Math.floor(Number(page) || 1)), pages);
  const start = (current - 1) * per;
  return { items: items.slice(start, start + per), page: current, pages, per, start,
           end: Math.min(start + per, items.length), total: items.length };
}
```

两处调用点改为：

```js
const pg = paginate(list, smsPoolState.page, smsPoolState.pageSize);
smsPoolState.page = pg.page;
const pages = pg.pages, start = pg.start, items = pg.items;
```

事件列表同理（替换了 308 字符的重复算术）。

### 2.3 验证

```
提取 3 个 <script> 块（总长 240051 字节）→ node --check：SYNTAX_OK
paginate 定义 True；调用点 2 处
服务重启后 active；/ 返回 302（正常跳登录）
```

### 2.4 为什么先做这个

账号表（`renderAccounts`，含勾选/徽章/Token 单元格）高度定制，
整体合流风险大；先合流结构简单、重复度高的两处，建立模式后再迁移大表。

下一轮：把 `renderAccounts` 的行渲染拆成"列定义 + 行模板"，
让账号表与 sms-pool 表共用同一套列渲染契约。
## 第 3 轮：账号表列定义化（15 列拆出模板字面量）

### 3.1 原状

`renderAccounts()` 里有一段 31 行的模板字面量 `rowHtmlV2`，把 15 个
`<td>` 全部硬写在一起：复选框、ID、邮箱（含三行子结构）、来源、Token、
密码、套餐、三个资格列、备注、2FA、Codex、时间、操作菜单。

想加一列要动整段字符串；想按列导出 CSV 得再抄一遍取值逻辑。

### 3.2 做法

拆成"列定义表 + 行渲染器"：

```js
const ACCOUNT_COLUMNS_V2 = [
  { cls: 'col-check', cell: (r) => `<input type="checkbox" ...>` },
  { cls: 'col-id', cell: (r) => `#${esc(r.id)}` },
  { cls: 'col-email',
    attrs: (r) => `title="..."`,      // 有额外属性的列单独给出
    cell: (r) => `<div class="acc-v2-email">...</div>` },
  ... 共 15 列
];

function renderAccountRowV2(r) {
  return '<tr>' + ACCOUNT_COLUMNS_V2.map((col) => {
    const attrs = typeof col.attrs === 'function' ? ' ' + col.attrs(r) : '';
    return `<td class="${col.cls}"${attrs}>${col.cell(r)}</td>`;
  }).join('') + '</tr>';
}
```

`renderAccounts()` 主体缩到 6 行，只负责取数据、写 `innerHTML`、更新选择态与分页。

### 3.3 验证（三层）

1. **语法**：提取 3 个 `<script>` 块（245561 字节）→ `node --check` SYNTAX_OK
2. **结构**：用 stub 在 node 里直接跑行渲染器
   ```
   td 数量: 15
   列类名: col-check,col-id,col-email,col-source,col-token,col-password,
          col-plan,col-qual,col-qual,col-qual,col-note,col-small,col-status,
          col-time,col-actions
   含复选框: true   含删除按钮: true
   ```
   与原模板的列顺序、类名完全一致。
3. **部署**：文件 8049 行；列定义 1 处、行渲染器 1 个；服务 active；`/` 302 正常。

### 3.4 过程中的一次事故（记录）

第一次用 `indexOf` 找模板起止标记时，文件是 CRLF 而我按 \`\n\` 匹配，
`startIdx = -1`，切片把文件写坏成两倍长度。已 `git checkout` 恢复，
改为"按行定位 + 保留原 EOL"重做。

教训：**在这类 400KB 单文件上做结构改动，必须按行定位并校验返回位置是否为正**，
不能直接对 `indexOf` 结果做切片。

### 3.5 收益

- 新增/删除列、调整列顺序只改数据表，不再动 HTML；
- 后续"按列导出 CSV"可以直接复用同一份 `cell` 取值逻辑；
- 账号表与 sms-pool 表共用 `paginate`，下一步可让它们共用同一列渲染契约。

下一轮（第 4 轮）：静态资源拆分（CSS/JS 外链 + 缓存头），首屏只加载所需。
## 第 4 轮：静态资源外链化 + 长缓存

### 4.1 之前的状态

`index.html` 单文件 8031 行：
- 3 个 `<style>` 块（128,562 字节 CSS）
- 3 个 `<script>` 块（245,563 字节 JS）

每次访问都要把这 374KB 一起下载，且**无法被浏览器缓存**
（HTML 本身不可缓存，内联资源随 HTML 一起失效）。

### 4.2 做法

1. 抽出 `webui/static/app.css`（129,003 字节）与 `webui/static/app.js`（260,235 字节）；
2. 模板里改为：
   ```html
   <link rel="stylesheet" href="/static/app.css?v=bd17de8f">   <!-- head 内 -->
   ...
   <script src="/static/app.js?v=be8a960a"></script>           <!-- </body> 前 -->
   ```
   版本号是内容 sha1 前 8 位，**内容变则 URL 变**，天然避免旧缓存；
3. `webui/app.py` 加 `SEND_FILE_MAX_AGE_DEFAULT = 604800`（7 天）。

### 4.3 一个必须注意的细节：脚本位置

原来 3 个 `<script>` 分散在第 3074 / 3125 / 3179 行，中间夹着 HTML。
合并成一个文件后如果仍放在第一处，会在部分 DOM 之前执行。
本次把外链脚本放到 `</body>` 之前——**比原来更晚，只会更安全**。

### 4.4 验证

```
模板: <style> 残留 0；<script> 只剩 1 个外链
语法: node --check webui/static/app.js → OK
结构: div 168/168、table 4/4、select 12/12、button 96/96 全部配对
资源: /static/app.css 200 (129,003B)，/static/app.js 200
缓存: Cache-Control: public, max-age=604800；Expires 7 天后
首页: / 仍 302（鉴权正常）
```

### 4.5 收益

- HTML 从 **419KB 降到 45KB**（首屏只解析结构，CSS/JS 走缓存）；
- CSS/JS 首次加载后 7 天内不再重复下载；
- 后续改样式/脚本不需要动模板（也不会让 HTML 缓存失效）。

### 4.6 过程中的一次事故

给 `app.py` 加配置时插到了函数体外，触发 `IndentationError`（line 355）。
立即按行修正缩进并 `py_compile` 通过。教训同第 3 轮：**改大文件后必须立刻编译校验**。

下一轮（第 5 轮）：交互细节对标（加载骨架屏、批量操作工具栏、快捷键），
并把 sms-pool 表也迁到同一列渲染契约。
