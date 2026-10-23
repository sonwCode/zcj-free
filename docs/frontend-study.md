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
