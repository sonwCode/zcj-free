function _qualInfo(r) {
  var pr = {};
  try {
    pr = typeof r.plan_check_result_json === 'string'
      ? JSON.parse(r.plan_check_result_json || '{}')
      : (r.plan_check_result_json || {});
  } catch (e) { pr = {}; }
  var plan = String(r.current_plan_type || pr.current_plan_type || '').toLowerCase();
  var checkedAt = String(r.plan_check_completed_at || pr.checked_at || '');
  var err = String(r.plan_check_error || '');
  var trialEligible = !!pr.plus_trial_eligible;
  var offers = Array.isArray(pr.eligible_offer_ids) ? pr.eligible_offer_ids.length : 0;
  var promos = (pr.eligible_promo_campaigns && typeof pr.eligible_promo_campaigns === 'object')
    ? Object.keys(pr.eligible_promo_campaigns).length : 0;
  // 优惠只看 promo_campaigns（真正的折扣活动）。
  // eligible_offer_ids 是"可升级套餐列表"，恒有若干项，不能当优惠用。
  var promoEligible = promos > 0;
  var NL = String.fromCharCode(10);
  var trialHtml;
  if (trialEligible) {
    var _disc = pr.plus_trial_discount_percentage;
    var _per = pr.plus_trial_duration_num_periods;
    var _unit = pr.plus_trial_duration_period ? String(pr.plus_trial_duration_period) : '';
    trialHtml = '<span class="qual-dot qual-dot--ok"></span>可领Plus试用'
      + (_disc ? '（' + _disc + '%' + (_per ? ' · ' + _per + (_unit || '期') : '') + '）' : '');
  }
  else if (checkedAt) { trialHtml = '<span class="qual-text-muted">无试用资格</span>'; }
  else { trialHtml = '<span class="qual-text-muted">信息不完整</span>'; }
  var promoHtml;
  if (!checkedAt) { promoHtml = '<span class="qual-text-muted">信息不完整</span>'; }
  else if (promoEligible) { promoHtml = '<span class="qual-tag qual-tag--ok">' + promos + ' 个活动</span>'; }
  else { promoHtml = '<span class="qual-text-muted">无可用优惠</span>'; }
  var checkHtml, checkTitle;
  if (err) {
    checkHtml = '<span class="qual-dot qual-dot--warn"></span>失败';
    checkTitle = '最近一次资格检查失败：' + err;
  } else if (!checkedAt) {
    checkHtml = '<span class="qual-text-muted">未探测</span>';
    checkTitle = '还没有做过资格检查';
  } else {
    // 最近检测时间必须按中国时间显示：后端存的是 UTC ISO，直接截断会显示成 08:xx。
    var when = String(formatDateTime(checkedAt) || '').slice(0, 16);
    checkHtml = when.slice(5);
    var tips = ['最近一次资格检查结果', '时间：' + when, '套餐：' + (plan || '-')];
    tips.push('可领 Plus 试用：' + (trialEligible ? '是' : '否'));
    if (pr.plus_trial_title) tips.push('活动：' + pr.plus_trial_title);
    if (pr.plus_trial_discount_percentage) tips.push('折扣：' + pr.plus_trial_discount_percentage + '%');
    tips.push('可升级套餐：' + offers + ' 个（非优惠）');
    tips.push('优惠活动：' + promos + ' 个');
    checkTitle = tips.join(NL);
  }
  var trialTitle = '试用资格：' + (trialEligible ? '可领 Plus 试用' : (checkedAt ? '不符合' : '尚未检测'))
    + (pr.plus_trial_title ? '（' + pr.plus_trial_title + '）' : '');
  var promoTitle = '优惠：' + (promoEligible ? (promos + ' 个活动') : (checkedAt ? '无可用优惠' : '尚未检测')) ;
  return { trialHtml: trialHtml, trialTitle: trialTitle,
           promoHtml: promoHtml, promoTitle: promoTitle,
           checkHtml: checkHtml, checkTitle: checkTitle };
}

;

// 确认弹窗：iframe 里原生 confirm() 会被静默拦截（直接 false），
// 而调用点是同步的 if (!confirm(x)) return; 结构，无法 await。
// 实现：首次点击弹出真实弹窗并返回 false；点"确定"后记录放行标记，
// 并自动重放刚才那次点击，让同一个 handler 第二次执行时通过。
(function () {
  var armed = new Map();
  var lastTarget = null;
  document.addEventListener('click', function (e) {
    var tgt = e.target;
    if (tgt && tgt.closest && tgt.closest('.ui-confirm-overlay')) return;
    lastTarget = tgt;
  }, true);
  window.uiConfirmSync = function (message) {
    var msg = String(message == null ? '' : message);
    var now = Date.now();
    if ((armed.get(msg) || 0) > 0 && now - armed.get(msg) < 8000) {
      armed.delete(msg);
      return true;
    }
    var overlay = document.createElement('div');
    overlay.className = 'ui-confirm-overlay';
    overlay.innerHTML = '<div class="ui-confirm-box" role="dialog" aria-modal="true">'
      + '<div class="ui-confirm-msg"></div>'
      + '<div class="ui-confirm-actions">'
      + '<button type="button" class="ui-confirm-cancel">取消</button>'
      + '<button type="button" class="ui-confirm-ok">确定</button>'
      + '</div></div>';
    overlay.querySelector('.ui-confirm-msg').textContent = msg;
    var okBtn = overlay.querySelector('.ui-confirm-ok');
    okBtn.style.background = '#dc2626';
    okBtn.style.borderColor = '#dc2626';
    function close() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    overlay.querySelector('.ui-confirm-cancel').addEventListener('click', close);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    okBtn.addEventListener('click', function () {
      armed.set(msg, Date.now());
      close();
      var el = lastTarget;
      if (el && el.isConnected) {
        try { el.click(); } catch (err) { console.warn('[confirm] 重放点击失败', err); }
      }
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    okBtn.focus();
    return false;
  };
})();

;

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let copySeq = 0;
const copyStore = new Map();
let ACCOUNTS = [], OUTLOOK = [], CONFIG = [];
let ACCOUNTS_TOTAL = 0;
const TOTP_CODE_CACHE = new Map();
let OUTLOOK_TOTAL = 0;
let SHOW_ARCHIVED_ACCOUNTS = false;
let SHOW_PLUS_ACCOUNTS_ONLY = false;
let SHOW_PLUS_TRIAL_ACCOUNTS_ONLY = false;
let SHOW_FREE_ACCOUNTS_ONLY = false;
const ACCOUNT_SELECTED = new Set();
const OUTLOOK_SELECTED = new Set();
let JOBS = [];
let JOBS_TOTAL = 0;
let JOB_STATUS_COUNTS = {};
let jobsRenderSignature = '';
const JOB_SELECTED = new Set();
let activeLogJob = null, logTimer = null, jobsTimer = null;

// ---------- 分页状态 ----------
const PAGERS = {
  jobs:     { page: 1, size: 20 },
  accounts: { page: 1, size: 20 },
  outlook:  { page: 1, size: 20 },
  codex:    { page: 1, size: 20 },
};

function _renderPager(id, total) {
  const p = PAGERS[id];
  const totalPages = Math.max(1, Math.ceil(total / p.size));
  if (p.page > totalPages) p.page = Math.max(1, totalPages);
  const start = total > 0 ? (p.page - 1) * p.size + 1 : 0;
  const end = Math.min(p.page * p.size, total);
  const sizes = [20, 50, 100];
  const html = `
    <button onclick="pagerGo('${id}',-1)"${p.page <= 1 ? ' disabled' : ''}>← 上一页</button>
    <span class="pager-info">${total > 0 ? `第 ${start}–${end} 条 / 共 ${total} 条（第 ${p.page} / ${totalPages} 页）` : '无数据'}</span>
    <button onclick="pagerGo('${id}',1)"${p.page >= totalPages ? ' disabled' : ''}>下一页 →</button>
    <select onchange="pagerSetSize('${id}',this.value)">${sizes.map(s =>
      `<option value="${s}"${p.size === s ? ' selected' : ''}>${s} 条/页</option>`
    ).join('')}</select>
    ${id === 'accounts' ? `<label class="pager-goto">前往
      <input type="number" min="1" max="${totalPages}" value="${p.page}" inputmode="numeric"
        onkeydown="if(event.key==='Enter'){pagerJump('${id}',this.value);event.preventDefault();}">
      页 <button type="button" onclick="pagerJump('${id}',this.previousElementSibling.value)">跳转</button>
    </label>` : ''}`;
  const el = document.getElementById('pager-' + id);
  if (el) el.innerHTML = html;

  if (id === 'jobs' || id === 'accounts' || id === 'codex' || id === 'outlook') {
    const elV2 = document.getElementById(
      id === 'jobs' ? 'pager-jobs-v2'
        : id === 'accounts' ? 'pager-accounts-v2'
        : id === 'codex' ? 'pager-codex-v2'
        : 'pager-outlook-v2'
    );
    if (elV2) {
      const sizeWrapId = id === 'jobs' ? 'jobsPagerSize'
        : id === 'accounts' ? 'accountsPagerSize'
        : id === 'codex' ? 'codexPagerSize'
        : 'outlookPagerSize';
      const v2Sizes = [10, 20, 30, 50, 100];
      const pages = [];
      let from = Math.max(1, p.page - 2);
      let to = Math.min(totalPages, from + 4);
      from = Math.max(1, to - 4);
      for (let i = from; i <= to; i++) {
        pages.push(i === p.page
          ? `<span class="pager-page">${i}</span>`
          : `<button type="button" onclick="pagerGoTo('${id}',${i})">${i}</button>`);
      }
      elV2.innerHTML = `
        <span class="pager-total">共 ${total} 条</span>
        <button type="button" onclick="pagerGo('${id}',-1)"${p.page <= 1 ? ' disabled' : ''} title="上一页">‹</button>
        ${pages.join('')}
        <button type="button" onclick="pagerGo('${id}',1)"${p.page >= totalPages ? ' disabled' : ''} title="下一页">›</button>
        <div class="pager-size" id="${sizeWrapId}">
          <button type="button" class="pager-size-btn" onclick="toggleV2PagerSize(event,'${sizeWrapId}')">${p.size}条/页</button>
          <div class="pager-size-menu" role="listbox">
            ${v2Sizes.map(s =>
              `<button type="button" class="pager-size-item${p.size === s ? ' is-active' : ''}" role="option" onclick="pickV2PagerSize('${id}','${sizeWrapId}',${s})">${s}条/页</button>`
            ).join('')}
          </div>
        </div>
        <label class="pager-goto">前往
          <input type="number" min="1" max="${totalPages}" value="${p.page}" inputmode="numeric"
            onkeydown="if(event.key==='Enter'){pagerJump('${id}',this.value);event.preventDefault();}">
          页 <button type="button" onclick="pagerJump('${id}',this.previousElementSibling.value)">跳转</button>
        </label>`;
    }
  }
}
function toggleV2PagerSize(e, wrapId) {
  e.stopPropagation();
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  const open = wrap.classList.toggle('open');
  if (open) {
    const closer = (ev) => {
      if (wrap.contains(ev.target)) return;
      wrap.classList.remove('open');
      document.removeEventListener('click', closer);
    };
    setTimeout(() => document.addEventListener('click', closer), 0);
  }
}
function pickV2PagerSize(id, wrapId, size) {
  const wrap = document.getElementById(wrapId);
  if (wrap) wrap.classList.remove('open');
  pagerSetSize(id, size);
}
function toggleJobsPagerSize(e) { toggleV2PagerSize(e, 'jobsPagerSize'); }
function pickJobsPagerSize(size) { pickV2PagerSize('jobs', 'jobsPagerSize', size); }
function pagerGoTo(id, page) {
  const p = PAGERS[id];
  if (!p) return;
  const total = id === 'jobs' ? JOBS_TOTAL
    : id === 'accounts' ? ACCOUNTS_TOTAL
    : id === 'outlook' ? OUTLOOK_TOTAL
    : id === 'codex' ? CODEX_TOTAL
    : (p.size || 20);
  const totalPages = Math.max(1, Math.ceil(Number(total || 0) / p.size) || 1);
  p.page = Math.max(1, Math.min(parseInt(page, 10) || 1, totalPages));
  _reloadPagedList(id);
}
function pagerJump(id, val) {
  pagerGoTo(id, val);
}

function _reloadPagedList(id) {
  ({ jobs: refreshJobs, accounts: loadAccounts, outlook: loadOutlook, codex: loadCodex })[id]?.();
}
function pagerGo(id, dir) {
  PAGERS[id].page = Math.max(1, PAGERS[id].page + dir);
  _reloadPagedList(id);
}
function pagerSetSize(id, val) {
  PAGERS[id].size = parseInt(val, 10) || 20;
  PAGERS[id].page = 1;
  _reloadPagedList(id);
}

// ---------- 工具 ----------
function fmt(v) { return v == null || v === '' ? '-' : String(v); }
function esc(v) { return fmt(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// 配置表单必须用这个：空值保持空，不能把 '' 显示成 '-'，否则一点保存就会写回 config
function attrEsc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function isPlaceholderEmpty(v) {
  const s = String(v == null ? '' : v).trim();
  return !s || ['-', '—', '无', '空', 'none', 'null', 'n/a', 'na'].includes(s.toLowerCase());
}
function short(v, n=40) { const s = v || ''; return s.length > n ? s.slice(0,n)+'…' : s; }
function copyId(v) { if (!v) return ''; const id = 'c'+(++copySeq); copyStore.set(id, v); return id; }
function cbtn(label, value, cls='') { const id = copyId(value); return `<button class="${cls}" data-copy-id="${id}" ${id?'':'disabled'}>${label}</button>`; }
function pill(status) {
  const map = { available:'可用', used:'已用', failed:'失败', partial_success:'部分成功', disabled:'已停用', pending:'排队', running:'运行中', stopping:'停止中', stopped:'已停止', success:'成功', cancelled:'已取消' };
  return `<span class="pill status-${esc(status)}">${esc(map[status]||status||'-')}</span>`;
}
function pillV2(status) {
  const s = String(status || '');
  const map = { available:'可用', used:'已用', failed:'失败', partial_success:'部分成功', disabled:'已停用', pending:'排队', running:'运行中', stopping:'停止中', stopped:'已停止', success:'成功', cancelled:'已取消' };
  let cls = 'jobs-v2-pill--muted';
  if (s === 'success') cls = 'jobs-v2-pill--success';
  else if (s === 'failed') cls = 'jobs-v2-pill--failed';
  else if (s === 'running' || s === 'stopping') cls = 'jobs-v2-pill--running';
  else if (s === 'pending' || s === 'cancelled' || s === 'stopped') cls = 'jobs-v2-pill--muted';
  return `<span class="jobs-v2-pill ${cls}">${esc(map[s]||s||'-')}</span>`;
}
function formatDateTime(v) {
  if (v == null || v === '') return '-';
  let d;
  if (v instanceof Date) {
    d = v;
  } else if (typeof v === 'number') {
    d = new Date(Math.abs(v) < 1e12 ? v * 1000 : v);
  } else {
    const raw = String(v).trim();
    if (!raw) return '-';
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
    const normalized = raw.replace(' ', 'T');
    const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : normalized + 'Z';
    d = new Date(withZone);
  }
  if (Number.isNaN(d.getTime())) {
    const s = String(v).replace('T', ' ').replace(/\.\d+.*$/, '').replace(/Z$/, '').replace(/[+-]\d{2}:\d{2}$/, '');
    return s || '-';
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(d).reduce((out, part) => { out[part.type] = part.value; return out; }, {});
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function formatTraffic(t) {
  if (!t || !t.available) return '-';
  const n = (v) => Number.isFinite(Number(v)) ? Math.max(0, Number(v)) : 0;
  const fmt = (v) => {
    v = n(v);
    if (v < 1024) return `${Math.round(v)} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KiB`;
    return `${(v / 1024 / 1024).toFixed(2)} MiB`;
  };
  const j = t.js_coverage;
  const jsHint = j && j.enabled ? `，JS脚本 ${n(j.script_count)}（执行 ${n(j.executed_script_count)}，候选 ${n(j.candidate_script_count)}）` : '';
  return `<span title="上传 ${fmt(t.upload_bytes)}，下载 ${fmt(t.download_bytes)}，${n(t.request_count)} 个请求${jsHint}">${fmt(t.total_bytes)}</span>`;
}
function jobsV2OpIcons() {
  return {
    view: `<svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
    retry: `<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
    stop: `<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`,
    check: `<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`,
    del: `<svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`,
  };
}
function showToast(t) { const el=$('#toast'); el.textContent=t; el.classList.add('show'); clearTimeout(showToast.t); showToast.t=setTimeout(()=>el.classList.remove('show'),1400); }
// 通用分页：把"算页数/夹住页码/切片"三件重复的事收在一处。
// 之前 renderSmsPoolEligible 与 renderSmsPoolEvents 各写一遍同样的算术。
// 加载骨架：账号表首次加载时先占位，避免"空白一大片"。
function skeletonRows(cols, rows) {
  const n = Math.max(1, Math.min(Number(rows) || 6, 20));
  const span = Math.max(1, Number(cols) || 1);
  const one = '<tr class="sk-row"><td colspan="' + span + '"><div class="sk-line"></div></td></tr>';
  return new Array(n).fill(one).join('');
}
function paginate(list, page, size) {
  const items = Array.isArray(list) ? list : [];
  const per = Math.max(1, Math.floor(Number(size) || 20));
  const pages = Math.max(1, Math.ceil(items.length / per));
  const current = Math.min(Math.max(1, Math.floor(Number(page) || 1)), pages);
  const start = (current - 1) * per;
  return { items: items.slice(start, start + per), page: current, pages, per, start,
           end: Math.min(start + per, items.length), total: items.length };
}
// 页面不可见时所有轮询直接跳过（省流量、省服务端）；回到前台立刻补一次，
// 避免"切回来看到的还是旧日志"。
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') return;
  const pollers = [() => typeof pollLog === 'function' && pollLog(),
                   () => typeof pollRetryLog === 'function' && pollRetryLog(),
                   () => typeof pollLiveLog === 'function' && pollLiveLog(),
                   () => typeof pollTotpLog === 'function' && pollTotpLog(),
                   () => typeof pollEmailChangeLog === 'function' && pollEmailChangeLog()];
  pollers.forEach((fn) => { try { fn(); } catch (e) { /* 忽略单点失败 */ } });
});
async function copyText(text) {
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else { const a=document.createElement('textarea'); a.value=text; a.style.position='fixed'; a.style.opacity='0'; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove(); }
    showToast('已复制');
  } catch(e) { showToast('复制失败'); }
}
const LOG_COPY_PLACEHOLDERS = new Set(['加载中…', '(暂无日志)', '(暂无日志，等待任务写入…)', '(暂无换绑日志)']);
function updateLogCopyButton(targetId) {
  const button = document.querySelector('[data-copy-log-target="' + targetId + '"]');
  const target = document.getElementById(targetId);
  if (!button || !target) return;
  const text = String(target.textContent || '').trim();
  button.disabled = !text || LOG_COPY_PLACEHOLDERS.has(text);
}
function copyLogFromPanel(button) {
  const target = document.getElementById(button.dataset.copyLogTarget || '');
  const text = String(target?.textContent || '').trim();
  if (!text || LOG_COPY_PLACEHOLDERS.has(text)) { showToast('暂无可复制日志'); return; }
  copyText(text);
}
function bindLogCopyButtons() {
  document.querySelectorAll('[data-copy-log-target]').forEach((button) => {
    const targetId = button.dataset.copyLogTarget;
    const target = document.getElementById(targetId);
    button.addEventListener('click', () => copyLogFromPanel(button));
    if (target) {
      const observer = new MutationObserver(() => updateLogCopyButton(targetId));
      observer.observe(target, { childList: true, characterData: true, subtree: true });
    }
    updateLogCopyButton(targetId);
  });
}
bindLogCopyButtons();
async function api(url, opts) {
  const r = await fetch(url, opts);
  const j = await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(j.error || ('HTTP '+r.status));
  return j;
}
function debounce(fn, wait=250) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

// ---------- Tab 切换 ----------
function activateTab(tab, persist=true) {
  const allowed = ['register','accounts','codex','outlook','sms_pool','config'];
  if (!allowed.includes(tab)) tab = 'register';
  $$('nav button').forEach(x => x.classList.toggle('active', x.dataset.tab === tab));
  allowed.forEach(t => $('#tab-'+t).classList.toggle('hidden', t !== tab));
  if (persist) localStorage.setItem('gpt_console_active_tab', tab);
  if (tab === 'accounts') loadAccounts();
  if (tab === 'codex') loadCodex();
  if (tab === 'outlook') loadOutlook();
  if (tab === 'config') loadConfig();
  if (tab === 'register') refreshJobs();
  if (tab === 'sms_pool') initSmsPoolTab();
}
$$('nav button').forEach(b => b.addEventListener('click', () => activateTab(b.dataset.tab)));

// ---------- 低价号码池（根页面 tab） ----------
const smsPoolState = { status: null, eligible: [], pending: [], reservations: [], events: [], page: 1, pageSize: 20, reservationPage: 1, eventPage: 1, busy: false, refreshing: false, eligibleLoaded: false, reservationsLoaded: false, initialized: false, timer: null };
const smsPoolEl = (id) => document.getElementById(id);
const smsPoolStatusNames = { reserved:'待分配', claimed:'已领取', running:'运行中', waiting_sms:'等待短信', completed:'已完成', released:'已释放', expired:'已过期', failed:'失败', orphaned:'待核对' };
function smsPoolMoney(value) { if (value == null || value === '') return '—'; const n = Number(value); return Number.isFinite(n) ? '¥' + n.toFixed(2) : '¥' + String(value); }
function smsPoolFmtTime(value) { if (!value) return '—'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', {hour12:false}); }
function smsPoolPill(label, tone='') { return '<span class="sms-pool-pill ' + tone + '">' + esc(label || '—') + '</span>'; }
function smsPoolTag(status) { const raw = String(status || ''); const tone = raw === 'completed' ? 'success' : ['reserved','claimed','running','waiting_sms'].includes(raw) ? 'pending' : ['failed','orphaned'].includes(raw) ? 'error' : ''; return smsPoolPill(smsPoolStatusNames[raw] || raw || '—', tone); }
function smsPoolNotice(message, tone='') { const el = smsPoolEl('smsPoolStateNotice'); if (!el) return; el.className = 'sms-pool-notice' + (tone ? ' ' + tone : ''); el.textContent = message; el.hidden = !message; }
function smsPoolLivePill(item) { const status = String(item.live_check_status || '').toLowerCase(); if (status === 'live' && item.live_check_ok) return smsPoolPill('正常', 'success'); if (status === 'queued' || status === 'running') return smsPoolPill('测活中', 'pending'); if (status === 'failed') return smsPoolPill('待重试', 'error'); return smsPoolPill('待测活', 'pending'); }

async function loadSmsPoolStatus() {
  try {
    const data = await api('/api/sms-pool/status');
    smsPoolState.status = data.status || {};
    const s = smsPoolState.status, cap = s.capacity || {}, stats = s.stats || {}, live = s.live_check || {}, summary = cap.live_summary || {};
    const providers = s.configured_providers || [], ready = providers.length > 0 && Boolean(s.max_price_cny);
    smsPoolEl('smsPoolStateDot').className = 'sms-pool-state-dot' + (s.enabled ? (s.paused ? ' paused' : ' on') : '');
    smsPoolEl('smsPoolStateText').textContent = s.enabled ? (s.paused ? '已启用 · 暂停新采购' : '已启用 · 自动轮询与补跑') : '已关闭 · 不会轮询或采购';
    smsPoolEl('smsPoolUpdatedAt').textContent = '刷新于 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false});
    smsPoolEl('smsPoolStatusUpdated').textContent = '已完成花费 ' + smsPoolMoney(stats.spent_cny_total || 0);
    smsPoolEl('smsPoolMetricEligible').textContent = cap.eligible ?? 0; smsPoolEl('smsPoolMetricLiveReady').textContent = summary.ready ?? 0; smsPoolEl('smsPoolMetricLiveWaiting').textContent = summary.waiting ?? 0; smsPoolEl('smsPoolMetricLiveChecking').textContent = summary.checking ?? 0;
    smsPoolEl('smsPoolMetricUnclaimed').textContent = stats.unclaimed ?? 0; smsPoolEl('smsPoolMetricRunning').textContent = (stats.running ?? 0) + ' / ' + (s.workers || 2); const __spBar = smsPoolEl('smsPoolMetricRunningBar'); if (__spBar) { const __spRun = Number(stats.running || 0), __spTot = Math.max(1, Number(s.workers || 2)); __spBar.style.width = Math.min(100, Math.round(__spRun / __spTot * 100)) + '%'; } smsPoolEl('smsPoolMetricDeactivated').textContent = summary.deactivated ?? 0;
    smsPoolEl('smsPoolCfgProviders').textContent = providers.length ? providers.join(' · ') : '未配置可用平台'; smsPoolEl('smsPoolCfgPrice').textContent = s.max_price_cny ? smsPoolMoney(s.max_price_cny) : '未设置 · 禁止采购'; smsPoolEl('smsPoolCfgCapacity').textContent = (s.poll_interval || 120) + ' 秒 · ' + (s.workers || 2) + ' 并发';
    renderSmsPoolPoll(s.poll || {});
    renderSmsPoolCooldowns(s.cooldowns || []);
    const pause = smsPoolEl('smsPoolBtnPause'); pause.disabled = !s.enabled; pause.innerHTML = s.paused ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 3 14 9-14 9z"/></svg><span>恢复采购</span>' : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg><span>暂停采购</span>';
    const toggle = smsPoolEl('smsPoolBtnToggle'); toggle.disabled = !s.enabled && !ready; toggle.classList.toggle('danger', Boolean(s.enabled)); toggle.classList.toggle('primary', !s.enabled); toggle.innerHTML = s.enabled ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/></svg><span>关闭号码池</span>' : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><span>启用号码池</span>';
    const interval = smsPoolEl('smsPoolLiveCheckInterval'), supported = ['1800','3600','21600','43200','86400']; smsPoolEl('smsPoolLiveCheckToggle').checked = Boolean(live.enabled); smsPoolEl('smsPoolLiveCheckToggleLabel').textContent = live.enabled ? '定时测活已开启' : '定时测活'; interval.value = supported.includes(String(live.interval)) ? String(live.interval) : '21600'; interval.disabled = smsPoolState.busy;
    if (!ready) smsPoolNotice('号码池缺少可用平台/API Key 或人民币单号上限，当前不会采购。请先在配置页补齐配置。', 'warn'); else if (!s.enabled) smsPoolNotice('号码池当前关闭。启用后将自动抢号、筛查账号活性，并在号码和账号均就绪时自动补跑。'); else if (s.paused) smsPoolNotice('号码池已暂停新采购；已锁定号码仍会自动分配，已开始的补跑不受影响。', 'warn'); else smsPoolNotice('号码池正在运行：新号码将自动分配给通过测活的账号；失效账号会从候选列表排除，账号记录保留。');
    smsPoolState.events = Array.isArray(data.events) ? data.events : [];
    renderSmsPoolEvents(smsPoolState.events);
  } catch (error) { smsPoolNotice('读取号码池状态失败：' + error.message, 'err'); }
}

function filteredSmsPoolAccounts() { const q = String(smsPoolEl('smsPoolAccountSearch').value || '').trim().toLocaleLowerCase(); return q ? smsPoolState.eligible.filter(x => String(x.email || '').toLocaleLowerCase().includes(q) || String(x.id || '').includes(q)) : smsPoolState.eligible; }
// sms-pool 候选表：与账号表同一套"列定义 + 行渲染"契约。
const SMS_POOL_ELIGIBLE_COLUMNS = [
  { cls: 'sms-pool-mono', head: 'ID', headCls: 'sms-pool-col-id', cell: (it) => '#' + esc(it.id) },
  { cls: 'sms-pool-email', head: '邮箱', headCls: 'sms-pool-col-email', cell: (it) => esc(it.email || '—') },
  { cls: '', head: '套餐', headCls: '', cell: (it) => esc(it.plan_type || 'free') },
  { cls: '', head: 'Codex', headCls: 'sms-pool-col-status',
    cell: (it) => smsPoolPill(it.codex_status || '未开始', it.codex_status === 'success' ? 'success' : '') },
  { cls: '', head: '账号测活', headCls: 'sms-pool-col-status', cell: (it) => smsPoolLivePill(it) },
  { cls: 'sms-pool-mono', head: '最近测活', headCls: 'sms-pool-col-time',
    cell: (it) => esc(smsPoolFmtTime(it.live_checked_at)) },
];

function renderSmsPoolEligibleRow(item) {
  const cells = SMS_POOL_ELIGIBLE_COLUMNS.map((col) =>
    '<td' + (col.cls ? ' class="' + col.cls + '"' : '') + '>' + col.cell(item) + '</td>').join('');
  return '<tr>' + cells + '</tr>';
}

function renderSmsPoolEligibleTable(rows) {
  const head = SMS_POOL_ELIGIBLE_COLUMNS.map((col) =>
    '<th' + (col.headCls ? ' class="' + col.headCls + '"' : '') + '>' + col.head + '</th>').join('');
  return '<table><thead><tr>' + head + '</tr></thead><tbody>' + rows + '</tbody></table>';
}
function renderSmsPoolEligible() {
  const list = filteredSmsPoolAccounts(); smsPoolState.pageSize = Number(smsPoolEl('smsPoolPageSize').value) || 20; const pg = paginate(list, smsPoolState.page, smsPoolState.pageSize); smsPoolState.page = pg.page; const pages = pg.pages; const start = pg.start; const items = pg.items;
  smsPoolEl('smsPoolEligibleCount').textContent = list.length + ' 个候选'; smsPoolEl('smsPoolEligibleSummary').textContent = list.length ? '显示 ' + (start + 1) + '–' + Math.min(start + smsPoolState.pageSize, list.length) + ' 条，共 ' + list.length + ' 条' : '暂无候选账号'; smsPoolEl('smsPoolPageInfo').textContent = smsPoolState.page + ' / ' + pages; smsPoolEl('smsPoolPagePrev').disabled = smsPoolState.page <= 1; smsPoolEl('smsPoolPageNext').disabled = smsPoolState.page >= pages;
  if (!items.length) { smsPoolEl('smsPoolEligibleBox').innerHTML = '<div class="sms-pool-empty">' + (smsPoolState.eligible.length ? '没有匹配账号' : '暂无符合条件的 free 未接码账号') + '</div>'; return; }
  const rows = items.map(renderSmsPoolEligibleRow).join('');
  smsPoolEl('smsPoolEligibleBox').innerHTML = renderSmsPoolEligibleTable(rows);
}
function renderSmsPoolPending() { const detail = smsPoolEl('smsPoolReviewDetails'); if (!smsPoolState.pending.length) { detail.hidden = true; return; } detail.hidden = false; smsPoolEl('smsPoolReviewSummary').textContent = '待核验账号 · ' + smsPoolState.pending.length; smsPoolEl('smsPoolReviewBox').innerHTML = '<table><thead><tr><th class="sms-pool-col-id">ID</th><th class="sms-pool-col-email">邮箱</th><th>原因</th></tr></thead><tbody>' + smsPoolState.pending.map(x => '<tr><td class="sms-pool-mono">#' + esc(x.id || '—') + '</td><td class="sms-pool-email">' + esc(x.email || '—') + '</td><td>' + esc(x.reason || '需核验') + '</td></tr>').join('') + '</tbody></table>'; }
async function loadSmsPoolEligible() { const box = smsPoolEl('smsPoolEligibleBox'); if (!smsPoolState.eligibleLoaded) box.innerHTML = '<div class="sms-pool-loading">正在加载账号…</div>'; try { const data = await api('/api/sms-pool/eligible'); smsPoolState.eligible = data.eligible || []; smsPoolState.pending = data.pending_review || []; smsPoolState.eligibleLoaded = true; renderSmsPoolEligible(); renderSmsPoolPending(); } catch (error) { if (!smsPoolState.eligibleLoaded) box.innerHTML = '<div class="sms-pool-error">账号列表读取失败：' + esc(error.message) + '</div>'; else smsPoolNotice('账号列表刷新失败，保留上次数据：' + error.message, 'warn'); smsPoolEl('smsPoolEligibleCount').textContent = '读取失败'; } }
async function loadSmsPoolReservations() { const box = smsPoolEl('smsPoolReservationsBox'); if (!smsPoolState.reservationsLoaded) box.innerHTML = '<div class="sms-pool-loading">正在加载号码记录…</div>'; const rawSize = Number(smsPoolEl('smsPoolReservationsPageSize').value); const size = Number.isFinite(rawSize) && rawSize > 0 ? Math.floor(rawSize) : 20; const rawPage = Number(smsPoolState.reservationPage); smsPoolState.reservationPage = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1; const offset = (smsPoolState.reservationPage - 1) * size; try { const data = await api('/api/sms-pool/reservations?limit=' + size + '&offset=' + offset); const items = data.items || []; const total = Number(data.total || 0); const pages = Math.max(1, Math.ceil((Number.isFinite(total) ? total : 0) / size)); if (!items.length && smsPoolState.reservationPage > 1) { smsPoolState.reservationPage = Math.min(smsPoolState.reservationPage - 1, pages); return loadSmsPoolReservations(); } smsPoolState.reservationPage = Math.min(smsPoolState.reservationPage, pages); if (!items.length) { box.innerHTML = '<div class="sms-pool-empty">暂无号码记录</div>'; } else { const rows = items.map(x => '<tr><td class="sms-pool-mono">#' + esc(x.id) + '</td><td>' + smsPoolTag(x.status) + '</td><td>' + esc(x.provider || '—') + '</td><td class="sms-pool-mono">' + esc(x.phone || '—') + '</td><td>' + esc(x.country_display || x.country || '—') + '</td><td class="sms-pool-mono">' + esc(x.account_id || '—') + '</td><td>' + esc(x.price_cny ? smsPoolMoney(x.price_cny) : '—') + '</td><td><button type="button" class="sms-pool-log-btn" data-sms-pool-reslog="' + esc(x.id) + '">查看日志</button></td><td>' + esc(x.failure_code || '—') + '</td><td class="sms-pool-col-time">' + esc(smsPoolFmtTime(x.created_at)) + '</td><td class="sms-pool-col-time">' + esc(smsPoolFmtTime(x.expires_at)) + '</td></tr>').join(''); box.innerHTML = '<table><thead><tr><th class="sms-pool-col-id">ID</th><th class="sms-pool-col-status">状态</th><th>平台</th><th>号码</th><th>地区</th><th>账号 ID</th><th>费用</th><th class="sms-pool-col-log">日志</th><th>失败分类</th><th class="sms-pool-col-time">获取日期</th><th class="sms-pool-col-time">有效期至</th></tr></thead><tbody>' + rows + '</tbody></table>'; } smsPoolState.reservationsLoaded = true; smsPoolEl('smsPoolReservationsSummary').textContent = total ? '显示 ' + (offset + 1) + '–' + Math.min(offset + items.length, total) + ' 条，共 ' + total + ' 条' : '暂无号码记录'; smsPoolEl('smsPoolReservationsPageInfo').textContent = smsPoolState.reservationPage + ' / ' + pages; smsPoolEl('smsPoolReservationsPrev').disabled = smsPoolState.reservationPage <= 1; smsPoolEl('smsPoolReservationsNext').disabled = smsPoolState.reservationPage >= pages; } catch (error) { if (!smsPoolState.reservationsLoaded) box.innerHTML = '<div class="sms-pool-error">号码记录读取失败：' + esc(error.message) + '</div>'; else smsPoolNotice('号码记录刷新失败，保留上次数据：' + error.message, 'warn'); } }
function renderSmsPoolEvents(events) { const rawSize = Number(smsPoolEl('smsPoolEventsPageSize').value); const size = Number.isFinite(rawSize) && rawSize > 0 ? Math.floor(rawSize) : 20; const grouped = {}; (events || []).forEach(x => { const key = String(x.reservation_id || '0'); if (!grouped[key] || Number(x.id || 0) > Number(grouped[key].id || 0)) grouped[key] = x; }); const all = Object.values(grouped).sort((a,b) => Number(b.id || 0) - Number(a.id || 0)); const pg = paginate(all, smsPoolState.eventPage, size); smsPoolState.eventPage = pg.page; const pages = pg.pages; const items = pg.items; smsPoolEl('smsPoolEventsSummary').textContent = all.length ? '显示 ' + ((smsPoolState.eventPage - 1) * size + 1) + '–' + Math.min(smsPoolState.eventPage * size, all.length) + ' 条，共 ' + all.length + ' 条' : '暂无运行事件'; smsPoolEl('smsPoolEventsPageInfo').textContent = smsPoolState.eventPage + ' / ' + pages; smsPoolEl('smsPoolEventsPrev').disabled = smsPoolState.eventPage <= 1; smsPoolEl('smsPoolEventsNext').disabled = smsPoolState.eventPage >= pages; if (!items.length) { smsPoolEl('smsPoolEventsBox').innerHTML = '<div class="sms-pool-empty">暂无运行事件</div>'; return; } smsPoolEl('smsPoolEventsBox').innerHTML = items.map(x => '<div class="sms-pool-event-row"><span class="sms-pool-event-id">#' + esc(x.reservation_id || '—') + '</span><div class="sms-pool-event-meta">' + smsPoolTag(x.event) + '<span>' + esc(smsPoolFmtTime(x.created_at)) + '</span></div><span class="sms-pool-event-detail" title="' + esc(x.detail || '—') + '">' + esc(x.detail || '—') + '</span><button type="button" class="sms-pool-log-btn" data-sms-pool-log="' + esc(x.reservation_id || '') + '">查看日志</button></div>').join(''); smsPoolEl('smsPoolEventsBox').querySelectorAll('[data-sms-pool-log]').forEach(btn => btn.addEventListener('click', () => viewSmsPoolLog(btn.dataset.smsPoolLog))); }
async function viewSmsPoolLog(reservationId) { try { const data = await api('/api/sms-pool/reservations/' + encodeURIComponent(reservationId) + '/events'); const lines = (data.items || []).map(x => smsPoolFmtTime(x.created_at) + '  ' + (smsPoolStatusNames[x.event] || x.event || '事件') + '  ' + (x.detail || '')); const modal = document.createElement('div'); modal.className = 'sms-pool-log-modal'; modal.innerHTML = '<div class="sms-pool-log-dialog" role="dialog" aria-modal="true"><div class="sms-pool-log-head"><strong>号码 #' + esc(reservationId) + ' 日志</strong><button type="button" class="sms-pool-log-close" aria-label="关闭">×</button></div><pre>' + esc(lines.join('\\n') || '暂无日志') + '</pre></div>'; document.body.appendChild(modal); const close = () => modal.remove(); modal.querySelector('.sms-pool-log-close').addEventListener('click', close); modal.addEventListener('click', e => { if (e.target === modal) close(); }); } catch (error) { showToast('日志读取失败'); } }
function smsPoolDuration(seconds) {
  const s = Math.max(0, Number(seconds || 0));
  if (s >= 3600) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h + ' 小时' + (m ? ' ' + m + ' 分' : ''); }
  if (s >= 60) return Math.round(s / 60) + ' 分钟';
  return s + ' 秒';
}
function renderSmsPoolCooldowns(items) {
  const box = smsPoolEl('smsPoolCooldownBox');
  if (!box) return;
  const list = Array.isArray(items) ? items : [];
  if (!list.length) { box.hidden = true; box.innerHTML = ''; return; }
  const shown = list.slice(0, 8);
  const chips = shown.map(x => {
    const name = smsPoolProviderName(x.provider) + (x.region ? ' · ' + esc(x.region) : (x.country ? ' · #' + esc(x.country) : ''));
    return '<span class="sms-pool-cooldown-chip" title="连续命中 ' + esc(x.streak || 0) + ' 次，共 ' + esc(x.count || 0) + ' 次">'
      + '<i class="dot"></i>' + esc(name) + '<small>' + esc(smsPoolDuration(x.remaining)) + '后恢复</small></span>';
  }).join('');
  const more = list.length > shown.length ? '<span class="sms-pool-cooldown-more">另 ' + (list.length - shown.length) + ' 项</span>' : '';
  box.hidden = false;
  box.innerHTML = '<span class="sms-pool-cooldown-label">临时冷却</span>' + chips + more;
}
function smsPoolProviderName(name) {
  const map = {smsbower:'SMSBower', tiger:'Tiger SMS', vaksms:'Vak SMS', herosms:'HeroSMS', smspool:'SMSPool'};
  return map[String(name || '').toLowerCase()] || String(name || '—');
}
function renderSmsPoolPoll(poll) {
  const box = smsPoolEl('smsPoolPollBox'); if (!box) return;
  const state = String(poll.status || 'unknown');
  const names = {waiting:'等待下一轮', polling:'轮询中', requesting:'请求平台', paused:'已暂停', stale:'worker 未运行', unknown:'未请求', idle:'本轮空闲', disabled:'未启用', error:'异常'};
  const providerNames = {smsbower:'SMSBower', tiger:'Tiger SMS', vaksms:'Vak SMS', herosms:'HeroSMS', smspool:'SMSPool'};
  const tone = ['polling','requesting'].includes(state) ? 'on' : state === 'error' ? 'err' : state === 'paused' ? 'paused' : '';
  const currentProvider = String(poll.current_provider || '').toLowerCase();
  const providerRows = (poll.providers || []).map(provider => {
    const key = String(provider || '').toLowerCase();
    const item = (poll.provider_state || {})[provider] || {};
    const ps = String(item.status || 'waiting');
    const pnames = {waiting:'待请求',requesting:'请求中',success:'已锁定',no_match:'未锁定',paused:'已暂停',stale:'worker 未运行',unknown:'未请求',idle:'未请求',error:'异常'};
    const statusClass = ['success','requesting','error','paused'].includes(ps) ? ps : '';
    const cardClass = ps === 'success' ? ' success' : ps === 'error' ? ' error' : '';
    const isCurrent = currentProvider === key && ps === 'requesting';
    const currentClass = isCurrent ? ' current' : '';
    const label = providerNames[key] || provider || '未知平台';
    const detail = item.last_result || (ps === 'waiting' ? '等待本轮请求' : '暂无最近结果');
    return '<article class="sms-pool-provider-card' + cardClass + currentClass + '" aria-label="' + esc(label) + '">' +
      '<div class="sms-pool-provider-card-top"><div class="sms-pool-provider-name"><span class="sms-pool-provider-marker" aria-hidden="true"></span><strong>' + esc(label) + '</strong></div>' +
      '<span class="sms-pool-provider-state ' + statusClass + '">' + esc(pnames[ps] || ps) + '</span></div>' +
      '<p class="sms-pool-provider-message" title="' + esc(detail) + '">' + esc(detail) + '</p></article>';
  }).join('');
  const action = poll.last_result || (currentProvider ? '最近处理：' + (providerNames[currentProvider] || currentProvider) : '等待 worker 状态');
  box.innerHTML = '<div class="sms-pool-poll-summary"><div class="sms-pool-poll-summary-main"><span class="sms-pool-poll-indicator ' + tone + '" aria-hidden="true"></span><div class="sms-pool-poll-summary-copy"><div class="sms-pool-poll-title"><strong>轮询状态：' + esc(names[state] || state) + '</strong><span class="sms-pool-poll-action" title="' + esc(action) + '">' + esc(action) + '</span></div></div></div><div class="sms-pool-poll-meta"><div class="sms-pool-poll-meta-item"><span class="sms-pool-poll-meta-label">最近心跳</span><strong class="sms-pool-poll-meta-value">' + esc(smsPoolFmtTime(poll.heartbeat_at)) + '</strong></div><div class="sms-pool-poll-meta-item"><span class="sms-pool-poll-meta-label">下次轮询</span><strong class="sms-pool-poll-meta-value">' + esc(smsPoolFmtTime(poll.next_poll_at)) + '</strong></div></div></div><div class="sms-pool-provider-grid">' + (providerRows || '<span class="sms-pool-last-updated">暂无已配置平台</span>') + '</div>' + (poll.error ? '<div class="sms-pool-poll-error">' + esc(poll.error) + '</div>' : '');
}
async function saveSmsPoolConfig(key, value) { smsPoolState.busy = true; smsPoolEl('smsPoolBtnToggle').disabled = true; smsPoolEl('smsPoolBtnPause').disabled = true; try { const result = await api('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({updates:{[key]:value}})}); smsPoolNotice(result.note || '配置已更新。'); await loadSmsPoolStatus(); } catch (error) { smsPoolNotice('保存配置失败：' + error.message, 'err'); } finally { smsPoolState.busy = false; } }
async function triggerSmsPoolLiveCheck() { const button = smsPoolEl('smsPoolBtnLiveCheck'); button.disabled = true; smsPoolEl('smsPoolLiveCheckFeedback').textContent = '正在加入后台查活队列…'; try { const result = await api('/api/sms-pool/live-check', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({limit:100})}); smsPoolEl('smsPoolLiveCheckFeedback').textContent = '已提交 ' + (result.queued || 0) + ' 个查活任务；失效账号会自动从候选中排除。'; await loadSmsPoolEligible(); } catch (error) { smsPoolEl('smsPoolLiveCheckFeedback').textContent = '提交查活失败：' + error.message; } finally { button.disabled = false; } }
async function refreshSmsPool() { if (smsPoolState.busy || smsPoolState.refreshing) return; smsPoolState.refreshing = true; try { await Promise.all([loadSmsPoolStatus(), loadSmsPoolEligible(), loadSmsPoolReservations()]); } finally { smsPoolState.refreshing = false; } }
function initSmsPoolTab() { if (!smsPoolState.initialized) { smsPoolState.initialized = true; smsPoolEl('smsPoolBtnRefresh').addEventListener('click', refreshSmsPool); smsPoolEl('smsPoolBtnPause').addEventListener('click', () => { if (smsPoolState.status) saveSmsPoolConfig('SMS_POOL_PAUSED', !smsPoolState.status.paused); }); smsPoolEl('smsPoolBtnToggle').addEventListener('click', () => { const s = smsPoolState.status; if (!s) return; if (s.enabled) { if (uiConfirmSync('关闭后将停止后续轮询和采购；已经锁定号码的补跑会继续执行。确定关闭吗？')) saveSmsPoolConfig('SMS_POOL_ENABLED', false); } else if (uiConfirmSync('启用后会开始按当前人民币单号上限轮询并采购号码，确定启用吗？')) saveSmsPoolConfig('SMS_POOL_ENABLED', true); }); smsPoolEl('smsPoolLiveCheckToggle').addEventListener('change', e => saveSmsPoolConfig('SMS_POOL_LIVE_CHECK_ENABLED', e.target.checked)); smsPoolEl('smsPoolLiveCheckInterval').addEventListener('change', e => saveSmsPoolConfig('SMS_POOL_LIVE_CHECK_INTERVAL', Number(e.target.value))); smsPoolEl('smsPoolBtnLiveCheck').addEventListener('click', triggerSmsPoolLiveCheck); smsPoolEl('smsPoolAccountSearch').addEventListener('input', () => { smsPoolState.page = 1; renderSmsPoolEligible(); }); smsPoolEl('smsPoolPageSize').addEventListener('change', () => { smsPoolState.page = 1; renderSmsPoolEligible(); }); smsPoolEl('smsPoolPagePrev').addEventListener('click', () => { smsPoolState.page = Math.max(1, smsPoolState.page - 1); renderSmsPoolEligible(); }); smsPoolEl('smsPoolPageNext').addEventListener('click', () => { smsPoolState.page += 1; renderSmsPoolEligible(); }); smsPoolEl('smsPoolReservationsPageSize').addEventListener('change', () => { smsPoolState.reservationPage = 1; loadSmsPoolReservations(); }); smsPoolEl('smsPoolReservationsPrev').addEventListener('click', () => { smsPoolState.reservationPage = Math.max(1, smsPoolState.reservationPage - 1); loadSmsPoolReservations(); }); smsPoolEl('smsPoolReservationsNext').addEventListener('click', () => { smsPoolState.reservationPage += 1; loadSmsPoolReservations(); }); smsPoolEl('smsPoolEventsPageSize').addEventListener('change', () => { smsPoolState.eventPage = 1; renderSmsPoolEvents(smsPoolState.events); }); smsPoolEl('smsPoolEventsPrev').addEventListener('click', () => { smsPoolState.eventPage = Math.max(1, smsPoolState.eventPage - 1); renderSmsPoolEvents(smsPoolState.events); }); smsPoolEl('smsPoolEventsNext').addEventListener('click', () => { smsPoolState.eventPage += 1; renderSmsPoolEvents(smsPoolState.events); }); } refreshSmsPool(); if (!smsPoolState.timer) smsPoolState.timer = setInterval(() => { if (!smsPoolEl('tab-sms_pool').classList.contains('hidden')) refreshSmsPool(); }, 15000); }

// ---------- 概览 ----------
async function loadSummary() {
  try {
    const s = await api('/api/summary');
    $('#statAccounts').textContent = s.accounts;
    $('#statOutlook').textContent = s.outlook_total;
    $('#statAvailable').textContent = s.outlook_available;
    $('#statUsed').textContent = s.outlook_used;
    $('#statFailed').textContent = s.outlook_failed;
  } catch(e) {}
}

// ---------- 注册 / 任务 ----------
async function startRegistrationFromInputs(countEl, workersEl, startBtn) {
  const count = parseInt((countEl?.value || '1'), 10);
  const workers = parseInt((workersEl?.value || '3'), 10);
  const emailSource = ($('#regEmailSourceV2')?.value || 'auto');
  const driver = ($('#regDriverV2')?.value || '');
  const activeCount = Number(JOB_STATUS_COUNTS.active || 0);
  // 不要用原生 confirm()：页面可能运行在 iframe 里，Chrome 会静默拦截 modal
  // （confirm 直接返回 false），表现就是"点了开始注册毫无反应"。
  if (activeCount > 0) {
    const warnEl0 = $('#regWarnV2');
    if (warnEl0) warnEl0.innerHTML = '<div class="banner info">当前已有 ' + activeCount
      + ' 个任务在跑或排队，本次再提交 ' + count + ' 个（合计 ' + (activeCount + count)
      + '）。如需清空残留可点"取消所有排队"。</div>';
  }
  if (startBtn) startBtn.disabled = true;
  const restoreBtn = () => {
    if (startBtn) startBtn.disabled = false;
    const b2 = $('#btnStartV2'); if (b2) b2.disabled = false;
  };
  const b2 = $('#btnStartV2'); if (b2) b2.disabled = true;
  try {
    const r = await api('/api/jobs', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({count, workers, email_source: emailSource, driver}) });
    const _splitOn = !!document.getElementById("regSplitV2")?.checked;
    const _warnText = _splitOn && r.warning
      ? "分裂已开启：邮箱按倍数派生子地址，忽略可用数不足提示"
      : r.warning;
    const warnHtml = _warnText ? `<div class="banner ${_splitOn ? "info" : "warn"}">${esc(_warnText)}（本次并发 ${r.workers || workers}）</div>` : `<div class="banner info">已提交 ${r.submitted} 个任务，本次并发 ${r.workers || workers}</div>`;
    const warnEl = $('#regWarnV2'); if (warnEl) warnEl.innerHTML = warnHtml;
    const c2 = $('#regCountV2'); if (c2) c2.value = count;
    const w2 = $('#regWorkersV2'); if (w2) w2.value = workers;
    refreshJobs();
  } catch(e) {
    const warnEl = $('#regWarnV2'); if (warnEl) warnEl.innerHTML = `<div class="banner warn">${esc(e.message)}</div>`;
  }
  finally { setTimeout(restoreBtn, 3000); }
}
async function loadRegistrationEmailSources() {
  const select = $('#regEmailSourceV2');
  if (!select) return;
  const selected = select.value || 'auto';
  try {
    const result = await api('/api/registration/email-sources');
    if (!Array.isArray(result.options)) return;
    select.innerHTML = result.options.map((option) =>
      '<option value="' + esc(option.value) + '">' + esc(option.label) + '</option>'
    ).join('');
    select.value = result.options.some((option) => option.value === selected) ? selected : 'auto';
  } catch (_) {}
}
async function cancelAllPendingJobs(btn) {
  const pendingCount = Number(JOB_STATUS_COUNTS.pending || 0);
  const msg = pendingCount > 0
    ? `确定取消 ${pendingCount} 个排队中的任务吗？已运行中的任务不受影响。`
    : '当前页没有排队任务；仍要请求后端取消所有排队任务吗？';
  if (!uiConfirmSync(msg)) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/jobs/cancel-pending', { method:'POST', headers:{'Content-Type':'application/json'}, body: '{}' });
    showToast(`已取消 ${r.cancelled} 个排队任务`);
    refreshJobs();
  } catch(e) { showToast('取消失败: ' + e.message); }
  finally {
    if (btn) btn.disabled = false;
    const b2 = $('#btnCancelPendingV2'); if (b2) b2.disabled = false;
  }
}
function syncJobsSelectAll(checked) {
  const pageRows = JOBS.filter(j => !['running','stopping'].includes(j.status));
  if (checked) pageRows.forEach(j => JOB_SELECTED.add(Number(j.id)));
  else pageRows.forEach(j => JOB_SELECTED.delete(Number(j.id)));
  renderJobs();
}
function bindJobsBodyEvents(bodyEl) {
  if (!bodyEl || bodyEl.dataset.bound === '1') return;
  bodyEl.dataset.bound = '1';
  bodyEl.addEventListener('pointerdown', (e) => {
    const retryBtn = e.target.closest('[data-retry-job]');
    if (!retryBtn || retryBtn.disabled || e.button !== 0) return;
    e.preventDefault();
    retryBtn.dataset.pointerHandled = '1';
    retryJob(parseInt(retryBtn.dataset.retryJob, 10), retryBtn);
  });
  bodyEl.addEventListener('click', (e) => {
    const logBtn = e.target.closest('[data-log-job]');
    if (logBtn) {
      openLog(parseInt(logBtn.dataset.logJob, 10));
      return;
    }
    const stopBtn = e.target.closest('[data-stop-job]');
    if (stopBtn) { stopJob(parseInt(stopBtn.dataset.stopJob, 10), stopBtn); return; }
    const otpBtn = e.target.closest('[data-submit-otp-job]');
    if (otpBtn) { submitManualOtpForJob(otpBtn); return; }
    const retryBtn = e.target.closest('[data-retry-job]');
    if (retryBtn) {
      if (retryBtn.dataset.pointerHandled === '1') {
        delete retryBtn.dataset.pointerHandled;
        return;
      }
      retryJob(parseInt(retryBtn.dataset.retryJob, 10), retryBtn);
      return;
    }
    const delBtn = e.target.closest('[data-delete-job]');
    if (delBtn) deleteJob(parseInt(delBtn.dataset.deleteJob, 10), delBtn);
  });
  bodyEl.addEventListener('change', (e) => {
    const cb = e.target.closest('.job-row-check');
    if (!cb) return;
    const id = Number(cb.dataset.jobId);
    if (cb.checked) JOB_SELECTED.add(id);
    else JOB_SELECTED.delete(id);
    updateJobsSelectionUi();
  });
}

// 注册任务区
(() => {
  const startV2 = $('#btnStartV2');
  loadRegistrationEmailSources();
  if (startV2) startV2.addEventListener('click', () => startRegistrationFromInputs($('#regCountV2'), $('#regWorkersV2'), startV2));
  const delV2 = $('#btnDeleteSelectedJobsV2');
  if (delV2) delV2.addEventListener('click', deleteSelectedJobs);
  const retryV2 = $('#btnRetrySelectedJobsV2');
  if (retryV2) retryV2.addEventListener('click', retrySelectedJobs);
  const cancelV2 = $('#btnCancelPendingV2');
  if (cancelV2) cancelV2.addEventListener('click', () => cancelAllPendingJobs(cancelV2));
  const refreshV2 = $('#btnRefreshJobsV2');
  if (refreshV2) refreshV2.addEventListener('click', refreshJobs);

  function stepInputNumber(inputId, delta) {
    const el = document.getElementById(inputId);
    if (!el) return;
    const min = Number(el.min || 1);
    const max = Number(el.max || 9999);
    const next = Math.min(max, Math.max(min, (parseInt(el.value, 10) || min) + delta));
    el.value = String(next);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  document.querySelectorAll('#jobsToolbarV2 [data-step-for], #accountsFilterV2 [data-step-for]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      stepInputNumber(btn.dataset.stepFor, parseInt(btn.dataset.step, 10) || 0);
    });
  });
  const selectAllV2 = $('#jobsSelectAllV2');
  if (selectAllV2) selectAllV2.addEventListener('change', (e) => syncJobsSelectAll(e.target.checked));
  bindJobsBodyEvents($('#jobsBodyV2'));
})();

function renderJobs() {
  const total = JOBS_TOTAL;
  const rows = JOBS;
  const bodyV2 = $('#jobsBodyV2');
  if (bodyV2) {
    const icons = jobsV2OpIcons();
    bodyV2.innerHTML = rows.map(j => {
      const running = ['running','stopping'].includes(j.status);
      const stoppable = ['pending','running','stopping'].includes(j.status);
      const started = formatDateTime(j.started_at);
      const completed = formatDateTime(j.completed_at);
      const err = j.error_message || '';
      const manualOtp = j.status === 'running' && j.email && j.manual_otp_required;
      return `
      <tr>
        <td class="col-check"><input type="checkbox" class="job-row-check" data-job-id="${esc(j.id)}" ${JOB_SELECTED.has(Number(j.id)) ? 'checked' : ''} ${running ? 'disabled title="运行中的任务不能选择"' : ''}></td>
        <td class="col-id">#${esc(j.id)}${j.parent_job_id ? `<div class="sub-cell" title="重试自任务 #${esc(j.parent_job_id)}">↳ #${esc(j.parent_job_id)} · ${esc(j.retry_attempt || 1)}</div>` : ''}</td>
        <td class="col-status">${pillV2(j.display_status || j.status)}</td>
        <td class="col-email" title="${esc(j.email || '-')}">${esc(j.email || '-')}</td>
        <td class="col-time" title="${esc(started)}">${esc(started)}</td>
        <td class="col-time" title="${esc(completed)}">${esc(completed)}</td>
        <td class="col-traffic">${formatTraffic(j.network_traffic)}</td>
        <td class="col-error" title="${esc(err)}">${esc(err ? short(err, 60) : '-')}</td>
        <td class="col-actions">
          <div class="jobs-v2-ops">
            <button type="button" class="jobs-v2-op jobs-v2-op--view" data-log-job="${esc(j.id)}" title="查看日志">${icons.view}</button>
            ${manualOtp ? `<input class="manual-otp-input manual-otp-input--v2" data-otp-email="${esc(j.email)}" data-otp-job="${esc(j.id)}" placeholder="邮箱验证码" maxlength="8" title="手动模式下，打开邮箱后把 6 位验证码贴这里"><button type="button" class="jobs-v2-op jobs-v2-op--otp" data-submit-otp-job="${esc(j.id)}" data-email="${esc(j.email)}" title="提交验证码">${icons.check}</button>` : ''}
            ${stoppable ? `<button type="button" class="jobs-v2-op jobs-v2-op--stop" data-stop-job="${esc(j.id)}" title="${j.status === 'stopping' ? '停止/修复' : '停止'}">${icons.stop}</button>` : ''}
            ${j.retryable ? `<button type="button" class="jobs-v2-op jobs-v2-op--retry" data-retry-job="${esc(j.id)}" title="${esc(j.retry_action === 'codex' ? '账号已创建，仅补跑 Codex 授权' : (j.retry_label || '重试'))}">${icons.retry}</button>` : ''}
            <button type="button" class="jobs-v2-op jobs-v2-op--del" data-delete-job="${esc(j.id)}" ${running ? 'disabled title="运行中的任务不能删除；如需删除请先停止"' : 'title="删除"'}>${icons.del}</button>
          </div>
        </td>
      </tr>`;
    }).join('') || '<tr><td colspan="9" style="text-align:center;color:#909399;padding:28px;">暂无任务</td></tr>';
  }


  updateJobsSelectionUi(rows);
  _renderPager('jobs', total);
}

function updateJobsSelectionUi(pageRows = null) {
  const bulkBtnV2 = $('#btnDeleteSelectedJobsV2');
  const bulkRetryBtnV2 = $('#btnRetrySelectedJobsV2');
  if (bulkBtnV2) bulkBtnV2.disabled = JOB_SELECTED.size === 0;
  if (bulkRetryBtnV2) bulkRetryBtnV2.disabled = JOB_SELECTED.size === 0;

  if (!pageRows) pageRows = JOBS;
  const selectableIds = pageRows.filter(j => !['running','stopping'].includes(j.status)).map(j => Number(j.id));
  const checkedCount = selectableIds.filter(id => JOB_SELECTED.has(id)).length;
  const cbAll = document.getElementById('jobsSelectAllV2');
  if (cbAll) {
    cbAll.checked = selectableIds.length > 0 && checkedCount === selectableIds.length;
    cbAll.indeterminate = checkedCount > 0 && checkedCount < selectableIds.length;
    cbAll.disabled = selectableIds.length === 0;
  }
}

async function refreshJobs() {
  try {
    const p = PAGERS.jobs;
    const res = await api(`/api/jobs?paged=1&page=${encodeURIComponent(p.page)}&page_size=${encodeURIComponent(p.size)}`);
    const nextJobs = res.items || [];
    JOBS_TOTAL = Number(res.total || nextJobs.length || 0);
    JOB_STATUS_COUNTS = res.status_counts || {};
    const totalPages = Math.max(1, Math.ceil(JOBS_TOTAL / p.size));
    if (p.page > totalPages) { p.page = totalPages; return refreshJobs(); }
    const nextSignature = JSON.stringify({items: nextJobs, total: JOBS_TOTAL, status_counts: JOB_STATUS_COUNTS, page: p.page, size: p.size});
    JOBS = nextJobs;
    if (nextSignature !== jobsRenderSignature) {
      jobsRenderSignature = nextSignature;
      renderJobs();
    }
    loadSummary();
  } catch(e) {}
}

let modalScrollY = 0;
function updateModalScrollLock() {
  const opened = !$('#logPanel').classList.contains('hidden')
    || !$('#retryLogPanel').classList.contains('hidden')
    || !$('#liveLogPanel').classList.contains('hidden')
    || !$('#totpLogPanel').classList.contains('hidden')
    || !$('#emailChangeLogPanel').classList.contains('hidden')
    || !$('#qrPanel').classList.contains('hidden')
    || !$('#outlookImportModal').classList.contains('hidden')
    || !$('#emailChangeModalV2').classList.contains('hidden');
  const locked = document.body.classList.contains('modal-open');
  if (opened && !locked) {
    modalScrollY = window.scrollY || document.documentElement.scrollTop || 0;
    document.body.style.top = `-${modalScrollY}px`;
    document.body.classList.add('modal-open');
    document.documentElement.style.overflow = 'hidden';
  } else if (!opened && locked) {
    document.body.classList.remove('modal-open');
    document.body.style.top = '';
    document.documentElement.style.overflow = '';
    window.scrollTo(0, modalScrollY || 0);
  }
}
function closeLogModal() {
  activeLogJob = null;
  clearInterval(logTimer);
  $('#logPanel').classList.add('hidden');
  updateModalScrollLock();
}
function closeRetryLogModal() {
  retryLogEmail = null;
  clearInterval(retryLogTimer);
  $('#retryLogPanel').classList.add('hidden');
  updateModalScrollLock();
}
function closeLiveLogModal() {
  liveLogEmail = null;
  clearInterval(liveLogTimer);
  $('#liveLogPanel').classList.add('hidden');
  updateModalScrollLock();
}
function openQrModal(url) {
  $('#qrImage').src = url;
  $('#qrOpenLink').href = url;
  $('#qrOpenLink').textContent = url;
  $('#qrPanel').classList.remove('hidden');
  updateModalScrollLock();
}
function closeQrModal() {
  $('#qrPanel').classList.add('hidden');
  $('#qrImage').removeAttribute('src');
  updateModalScrollLock();
}

$('#btnCloseLog').addEventListener('click', closeLogModal);

async function submitManualOtpForJob(btn) {
  const jobId = parseInt(btn.dataset.submitOtpJob, 10);
  const email = btn.dataset.email || '';
  const input = document.querySelector(`.manual-otp-input[data-otp-job="${jobId}"]`);
  const code = (input?.value || '').trim();
  if (!code) { showToast('请先填写邮箱验证码'); return; }
  btn.disabled = true;
  try {
    const r = await api('/api/manual-otp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email, code, job_id: jobId }),
    });
    showToast(r.ok ? `已提交验证码给 ${email || ('#'+jobId)}` : (r.error || '提交失败'));
    if (input) input.value = '';
    if (activeLogJob === jobId) pollLog();
  } catch (e) {
    showToast('提交验证码失败: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function stopJob(jobId, btn) {
  const job = JOBS.find(j => Number(j.id) === Number(jobId));
  const statusText = job?.status || '-';
  const emailText = job?.email || '未分配邮箱';
  if (!uiConfirmSync(`确定停止任务 #${jobId}？\n\n状态：${statusText}\n邮箱：${emailText}\n\n排队任务会直接取消；运行中的任务会发送停止信号，并在当前步骤检查点退出。`)) return;
  btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${jobId}/stop`, { method:'POST', headers:{'Content-Type':'application/json'}, body: '{}' });
    showToast(r.message || '已发送停止信号');
    refreshJobs();
    if (activeLogJob === jobId) pollLog();
  } catch(e) {
    showToast('停止失败: ' + e.message);
    btn.disabled = false;
  }
}

async function deleteJob(jobId, btn) {
  const job = JOBS.find(j => Number(j.id) === Number(jobId));
  const statusText = job?.status || '-';
  const emailText = job?.email || '未分配邮箱';
  if (!uiConfirmSync(`确定删除任务 #${jobId}？\n\n状态：${statusText}\n邮箱：${emailText}\n\n会同时删除该任务日志。排队任务删除后将不会执行。`)) return;
  btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${jobId}/delete`, { method:'POST', headers:{'Content-Type':'application/json'}, body: '{}' });
    if (activeLogJob === jobId) closeLogModal();
    showToast(r.deleted ? '任务已删除' : '任务不存在');
    JOB_SELECTED.delete(Number(jobId));
    refreshJobs();
  } catch(e) {
    showToast('删除失败: ' + e.message);
    btn.disabled = false;
  }
}

async function deleteSelectedJobs() {
  const ids = Array.from(JOB_SELECTED);
  if (ids.length === 0) { showToast('请先选择任务'); return; }
  const msg = `确定删除选中的 ${ids.length} 个任务吗？\n\n` +
              `会同时删除对应日志。排队/运行中的任务会由后端逐条校验，不能删除的会自动跳过。`;
  if (!uiConfirmSync(msg)) return;

  const bulkDel = $('#btnDeleteSelectedJobsV2');
  if (bulkDel) bulkDel.disabled = true;
  try {
    const r = await api('/api/jobs/delete-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({job_ids: ids}),
    });
    (r.deleted || []).forEach(id => JOB_SELECTED.delete(Number(id)));
    if (activeLogJob != null && (r.deleted || []).map(Number).includes(Number(activeLogJob))) closeLogModal();
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已删除 ${r.deleted_count || 0} 个，跳过 ${skippedCount} 个` : `已删除 ${r.deleted_count || 0} 个`);
    refreshJobs();
  } catch(e) {
    showToast('批量删除失败: ' + e.message);
    updateJobsSelectionUi();
  }
}

async function retryJob(jobId, btn) {
  const job = JOBS.find(j => Number(j.id) === Number(jobId));
  if (!job || !job.retryable) { showToast('该任务当前不可重试'); return; }
  const actionText = job.retry_action === 'codex' ? '仅补跑 Codex 授权' : '创建新的完整注册任务';
  if (!uiConfirmSync(`确定${job.retry_label || '重试'}任务 #${jobId}？\n\n${actionText}\n原任务记录和日志会保留。`)) return;
  btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${jobId}/retry`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    showToast(r.message || '已提交重试任务');
    JOB_SELECTED.delete(Number(jobId));
    refreshJobs();
  } catch (e) {
    showToast('重试失败: ' + e.message);
    btn.disabled = false;
  }
}

async function retrySelectedJobs() {
  const ids = Array.from(JOB_SELECTED);
  if (!uiConfirmSync(`确定重试选中的 ${ids.length} 个任务吗？\n\n后端会逐条校验；已创建账号的任务只补跑 Codex，其余任务重新入队注册，不能重试的会自动跳过。`)) return;
  const btn = $('#btnRetrySelectedJobsV2');
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/jobs/retry-bulk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_ids: ids}),
    });
    ids.forEach(id => JOB_SELECTED.delete(Number(id)));
    const skippedCount = (r.skipped || []).length;
    const reusedCount = r.reused_count || 0;
    showToast(`已提交 ${r.started_count || 0} 个${reusedCount ? `，复用 ${reusedCount} 个` : ''}${skippedCount ? `，跳过 ${skippedCount} 个` : ''}`);
    refreshJobs();
  } catch (e) {
    showToast('批量重试失败: ' + e.message);
  } finally {
    updateJobsSelectionUi();
  }
}

function openLog(jobId) {
  activeLogJob = jobId;
  $('#logJobId').textContent = jobId;
  $('#logPanel').classList.remove('hidden');
  updateModalScrollLock();
  $('#logContent').textContent = '加载中…';
  pollLog();
  clearInterval(logTimer);
  logTimer = setInterval(pollLog, 5000);
}
async function pollLog() {
  if (activeLogJob == null) return;
  if (document.visibilityState === 'hidden') return;
  try {
    const r = await api(`/api/jobs/${activeLogJob}/log`);
    const c = $('#logContent');
    const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
    c.textContent = r.log || '(暂无日志)';
    if (atBottom) c.scrollTop = c.scrollHeight;
    if (r.job && ['success','failed','stopped','cancelled'].includes(r.job.status)) clearInterval(logTimer);
  } catch(e) {}
}

// ---------- 账号 ----------
let accountsLoading = false;
let planStatusLoading = false;
let planStatusRevision = '';
function getAccountsQuery() {
  const el = document.getElementById('qAccountsV2');
  return (el ? el.value : '').trim();
}
function getAccountsCodexFilter() {
  const el = document.getElementById('codexStatusFilterV2');
  return (el ? el.value : '').trim();
}
function getAccountsTotpFilter() {
  const el = document.getElementById('totpStatusFilterV2');
  return (el ? el.value : '').trim();
}
function getCodexBulkWorkers() {
  const el = document.getElementById('codexBulkWorkersV2');
  return Math.max(1, Math.min(16, Number((el && el.value) || 3)));
}
function getAccountsPlanFilter() {
  if (SHOW_PLUS_ACCOUNTS_ONLY) return 'plus';
  if (SHOW_PLUS_TRIAL_ACCOUNTS_ONLY) {
    const type = (document.getElementById('promoTypeFilterV2')?.value || '').trim().toLowerCase();
    const discount = (document.getElementById('promoDiscountFilterV2')?.value || '').trim();
    return (type || discount) ? `promo:${type || '*'}:${discount}` : 'promo';
  }
  if (SHOW_FREE_ACCOUNTS_ONLY) return 'free_no_trial';
  return '';
}
// 一键清理已废账号：直接用查活已经写进库里的结论（live_check_status=deactivated）。
// 不再额外探活，避免把正常号误判成死号。
async function cleanupDeadAccounts() {
  var NL = String.fromCharCode(10);
  var btn = $('#btnCleanupDeadV2');
  if (btn) { btn.disabled = true; btn.textContent = '统计中…'; }
  try {
    var preview = await api('/api/accounts/cleanup-dead', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dry_run: true, verify: false }),
    });
    if (!preview || !preview.ok) { showToast('统计失败：' + ((preview && preview.error) || '未知错误')); return; }
    var count = Number(preview.count || 0);
    var alive = Number(preview.alive || 0);
    if (!count) { showToast('没有查活已废的账号（正常 ' + alive + ' 个）'); return; }
    var sample = (preview.sample || []).slice(0, 5).map(function (s) {
      return '#' + s.id + ' ' + (s.email || '');
    }).join('、');
    var msg = '将清理 ' + count + ' 个已废账号' + NL + NL +
      '依据：你查活得出的结论（账号已废）' + NL +
      '其余 ' + alive + ' 个正常账号不会被删除' + NL +
      (sample ? ('例如：' + sample + NL) : '') +
      NL + '删除后不可恢复。';
    if (!uiConfirmSync(msg)) return;
    var r = await api('/api/accounts/cleanup-dead', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dry_run: false, mode: 'delete', verify: false }),
    });
    if (r && r.ok) {
      showToast('已清理 ' + (r.deleted_count || 0) + ' 个已废账号');
      if (typeof loadAccounts === 'function') await loadAccounts();
    } else {
      showToast('清理失败：' + ((r && r.error) || '未知错误'));
    }
  } catch (err) {
    showToast('清理异常：' + (err && err.message ? err.message : err));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '清理已废'; }
  }
}

async function loadAccounts() {
  loadFunnelV2();
  if (accountsLoading) return;
  accountsLoading = true;
  try {
    if (!ACCOUNTS.length) {
      const body = $('#accountsBodyV2');
      if (body) body.innerHTML = skeletonRows(15, 8);
    }
    const archived = SHOW_ARCHIVED_ACCOUNTS ? 'only' : '0';
    const plan = getAccountsPlanFilter();
    const codex = getAccountsCodexFilter();
    const totp = getAccountsTotpFilter();
    const q = getAccountsQuery();
    const dateFrom = document.getElementById('dateFromAccountsV2')?.value || '';
    const dateTo = document.getElementById('dateToAccountsV2')?.value || '';
    const p = PAGERS.accounts;
    const res = await api(`/api/accounts?paged=1&page=${encodeURIComponent(p.page)}&page_size=${encodeURIComponent(p.size)}&archived=${encodeURIComponent(archived)}&plan=${encodeURIComponent(plan)}&codex_status=${encodeURIComponent(codex)}&totp_status=${encodeURIComponent(totp)}&q=${encodeURIComponent(q)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`);
    ACCOUNTS = res.items || [];
    ACCOUNTS_TOTAL = Number(res.total || ACCOUNTS.length || 0);
    const totalPages = Math.max(1, Math.ceil(ACCOUNTS_TOTAL / p.size));
    if (p.page > totalPages) { p.page = totalPages; accountsLoading = false; return loadAccounts(); }
    renderAccounts();
  } catch(e) { showToast('加载账号失败: ' + e.message); }
  finally { accountsLoading = false; }
}
async function pollAccountPlanStatuses() {
  if (planStatusLoading || accountsLoading) return;
  planStatusLoading = true;
  try {
    const archived = SHOW_ARCHIVED_ACCOUNTS ? 'only' : '0';
    const plan = getAccountsPlanFilter();
    const codex = getAccountsCodexFilter();
    const totp = getAccountsTotpFilter();
    const q = getAccountsQuery();
    const dateFrom = document.getElementById('dateFromAccountsV2')?.value || '';
    const dateTo = document.getElementById('dateToAccountsV2')?.value || '';
    const p = PAGERS.accounts;
    const snapshot = await api(`/api/accounts/plan-check-status?page=${encodeURIComponent(p.page)}&page_size=${encodeURIComponent(p.size)}&archived=${encodeURIComponent(archived)}&plan=${encodeURIComponent(plan)}&codex_status=${encodeURIComponent(codex)}&totp_status=${encodeURIComponent(totp)}&q=${encodeURIComponent(q)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`);
    const items = snapshot.items || [];
    const accountById = new Map(ACCOUNTS.map(r => [Number(r.id), r]));
    const hasUnknown = items.some(item => !accountById.has(Number(item.id)));
    if (hasUnknown || items.length !== ACCOUNTS.length || Number(snapshot.total || 0) !== ACCOUNTS_TOTAL) {
      planStatusRevision = snapshot.revision || '';
      await loadAccounts();
      return;
    }
    if ((snapshot.revision || '') !== planStatusRevision) {
      let needsFullReload = false;
      items.forEach(item => {
        const account = accountById.get(Number(item.id));
        const wasChecking = ['queued', 'running'].includes(account?.plan_check_status);
        const isChecking = ['queued', 'running'].includes(item.plan_check_status);
        const wasLiveChecking = ['queued', 'running'].includes(account?.live_check_status);
        const isLiveChecking = ['queued', 'running'].includes(item.live_check_status);
        const wasExtracting = ['queued', 'running'].includes(account?.extract_link_status);
        const isExtracting = ['queued', 'running'].includes(item.extract_link_status);
        const wasAgenting = ['queued', 'running'].includes(account?.codex_agent_status);
        const isAgenting = ['queued', 'running'].includes(item.codex_agent_status);
        if (wasChecking && !isChecking) needsFullReload = true;
        // 查活状态也在此轻量快照轮询。任务结束后完整刷新一次，确保 AT 可用状态等列表字段同步。
        if (wasLiveChecking && !isLiveChecking) needsFullReload = true;
        if (wasExtracting && !isExtracting) needsFullReload = true;
        if (wasAgenting && !isAgenting) needsFullReload = true;
        Object.assign(account, item);
      });
      planStatusRevision = snapshot.revision || '';
      if (needsFullReload) await loadAccounts();
      else renderAccounts();
    }
  } catch(e) {}
  finally { planStatusLoading = false; }
}
function _codexCell(r) {
  const s = r.codex_status || '';
  const err = r.codex_error || '';
  const titleAttr = err ? ` title="${esc(err)}"` : '';
  if (s === 'success') return `<span class="pill status-success">成功</span>`;
  if (s === 'retrying') return `<span class="pill status-running">补跑中</span>`;
  if (s === 'stopped') return `<span class="pill status-used"${titleAttr}>已停止</span>`;
  if (s === 'failed') return `<span class="pill status-failed"${titleAttr}>失败</span>`;
  if (s === 'skipped') return `<span class="pill status-used">已跳过</span>`;
  if (s === 'deactivated') return `<span class="pill status-failed" title="授权记录显示账号异常，建议通过查活确认账号状态">失败</span>`;
  return `<span class="muted">-</span>`;
}
function _codexMoneyLabel(value, symbol) {
  const text = String(value ?? '').trim();
  if (!text) return '—';
  const amount = text.replace(/^[\$¥￥]\s*/, '').replace(/\s*(?:USD|CNY|RMB)$/i, '').trim();
  return amount ? amount + symbol : '—';
}
function _codexPhonePriceDisplay(price, priceCny) {
  const original = String(price ?? '').trim();
  const cny = String(priceCny ?? '').trim();
  if (!original) return cny ? cny + ' ￥' : '—';
  return cny ? original + ' / ' + cny + ' ￥' : original;
}
function _codexAccountInfoCellV2(r) {
  if (String(r.codex_status || '').toLowerCase() !== 'success') return '';
  const info = r.codex_account_info && typeof r.codex_account_info === 'object'
    ? r.codex_account_info : {};
  const phone = String(info.phone_number ?? '').trim();
  if (!phone) return '';
  const region = String(info.region ?? '').trim() || '未知';
  const priceParts = [];
  const cny = _codexMoneyLabel(info.price_cny, '¥');
  const usd = _codexMoneyLabel(info.price_usd, '$');
  if (cny !== '—') priceParts.push(cny);
  if (usd !== '—') priceParts.push(usd);
  const price = priceParts.length ? priceParts.join('/') : '未知';
  const limitSymbol = String(info.price_limit_currency ?? '').toUpperCase() === 'USD' ? '$' : '';
  const apiLimit = _codexMoneyLabel(info.price_limit_max, limitSymbol);
  const configuredLimit = _codexMoneyLabel(info.price_limit_configured_max, '¥');
  const limitText = configuredLimit !== '—'
    ? configuredLimit + (apiLimit !== '—' ? ' (' + apiLimit + ')' : '')
    : apiLimit;
  const priceDisplay = limitText !== '—' ? price + ' / 上限 ' + limitText : price;
  const titleParts = [];
  const provider = String(info.provider ?? '').trim();
  const source = String(info.price_source ?? '').trim();
  const fxSource = String(info.price_limit_fx_source ?? '').trim();
  const fxRate = String(info.price_limit_fx_rate ?? '').trim();
  if (provider) titleParts.push('短信平台: ' + provider);
  if (fxRate) titleParts.push('CNY/USD: ' + fxRate);
  if (fxSource) titleParts.push('汇率来源: ' + fxSource);
  const rateDate = String(info.rate_date ?? '').trim();
  if (source) titleParts.push('价格来源: ' + source);
  if (rateDate) titleParts.push('USD/CNY 汇率日期: ' + rateDate);
  const titleAttr = titleParts.length ? ' title="' + esc(titleParts.join('；')) + '"' : '';
  return '<div class="acc-v2-codex-info"' + titleAttr + '>'
    + '<div class="acc-v2-codex-info-line"><span class="acc-v2-codex-info-label">手机号：</span>' + esc(phone) + '</div>'
    + '<div class="acc-v2-codex-info-line"><span class="acc-v2-codex-info-label">归属地：</span>' + esc(region) + '</div>'
    + '<div class="acc-v2-codex-info-line"><span class="acc-v2-codex-info-label">价格：</span>' + esc(priceDisplay) + '</div></div>';
}
function _codexCredentialPhoneInfoV2(r) {
  const phone = String(r.phone ?? '').trim();
  const country = String(r.phone_country ?? '').trim();
  const price = _codexPhonePriceDisplay(r.phone_price, r.phone_price_cny);
  if (!phone && !country && price === '—') return '<span class="acc-v2-muted">-</span>';
  const title = [phone && '手机号: ' + phone, country && '归属地: ' + country, price !== '—' && '价格: ' + price]
    .filter(Boolean).join('；');
  const titleAttr = title ? ' title="' + esc(title) + '"' : '';
  return '<div class="codex-phone-info-v2"' + titleAttr + '>'
    + '<div class="codex-phone-info-v2-line"><span class="codex-phone-info-v2-label">手机号：</span>' + esc(phone || '—') + '</div>'
    + '<div class="codex-phone-info-v2-line"><span class="codex-phone-info-v2-label">归属地：</span>' + esc(country || '—') + '</div>'
    + '<div class="codex-phone-info-v2-line"><span class="codex-phone-info-v2-label">价格：</span>' + esc(price || '—') + '</div></div>';
}
function _codexCellV2(r) {
  const s = String(r.codex_status || '').toLowerCase();
  const err = r.codex_error || '';
  const titleAttr = err ? ` title="${esc(err)}"` : '';
  let authHtml = '<span class="acc-v2-muted">-</span>';
  if (s === 'success') authHtml = `<span class="acc-v2-codex is-ok"${titleAttr}>已授权</span>`;
  else if (s === 'retrying') authHtml = `<span class="acc-v2-codex is-run"${titleAttr}>补跑中</span>`;
  else if (s === 'stopped') authHtml = `<span class="acc-v2-codex is-mute"${titleAttr}>已停止</span>`;
  else if (s === 'failed') authHtml = `<span class="acc-v2-codex is-fail"${titleAttr}>授权失败</span>`;
  else if (s === 'skipped') authHtml = `<span class="acc-v2-codex is-skip"${titleAttr}>已跳过</span>`;
  else if (s === 'deactivated') authHtml = '<span class="acc-v2-codex is-fail" title="授权记录显示账号异常，建议分别执行账号查活和 Codex OAuth 测活">授权异常</span>';

  const accountInfoHtml = _codexAccountInfoCellV2(r);
  const oauthStatus = r.codex_oauth_check_status || '';
  if (!oauthStatus) return authHtml + accountInfoHtml;
  const oauthTitle = [
    r.codex_oauth_checked_at ? `检测时间: ${formatDateTime(r.codex_oauth_checked_at)}` : '',
    r.codex_oauth_check_http_status != null ? `HTTP: ${r.codex_oauth_check_http_status}` : '',
    r.codex_oauth_check_cpa_name ? `CPA: ${r.codex_oauth_check_cpa_name}` : '',
    r.codex_oauth_check_error || '',
  ].filter(Boolean).join('\n');
  const oa = oauthTitle ? ` title="${esc(oauthTitle)}"` : '';
  let oauthHtml = '';
  if (oauthStatus === 'valid') oauthHtml = `<div class="acc-v2-sub" style="color:#67c23a"${oa}>OAuth: 有效</div>`;
  else if (oauthStatus === 'invalid') oauthHtml = `<div class="acc-v2-sub" style="color:#f56c6c"${oa}>OAuth: 失效</div>`;
  else if (oauthStatus === 'missing') oauthHtml = `<div class="acc-v2-sub" style="color:#909399"${oa}>OAuth: 无凭证</div>`;
  else if (oauthStatus === 'queued') oauthHtml = `<div class="acc-v2-sub" style="color:#e6a23c"${oa}>OAuth: 排队</div>`;
  else if (oauthStatus === 'running') oauthHtml = `<div class="acc-v2-sub" style="color:#e6a23c"${oa}>OAuth: 检测中</div>`;
  else if (oauthStatus === 'failed') oauthHtml = `<div class="acc-v2-sub" style="color:#e6a23c"${oa}>OAuth: 检测失败</div>`;
  return authHtml + accountInfoHtml + oauthHtml;
}
function _totpCellV2(r) {
  const enabled = !!r.totp_enabled;
  const status = r.totp_setup_status || '';
  const errorCode = r.totp_setup_error_code || '';
  const titleText = [r.totp_setup_message, r.totp_setup_error, errorCode].filter(Boolean).join('\n');
  const titleAttr = titleText ? ` title="${esc(titleText)}"` : '';
  if (enabled) {
    const cached = TOTP_CODE_CACHE.get(Number(r.id));
    const codeHtml = cached?.code
      ? `<span class="acc-v2-twofa-code" title="${esc(cached.at || '')}">${esc(cached.code)}</span><button type="button" class="acc-v2-twofa-btn" data-account-show-totp-code="${esc(r.id)}" title="刷新当前验证码">刷新</button><button type="button" class="acc-v2-twofa-btn" data-account-copy-shown-totp-code="${esc(r.id)}" title="复制当前显示的验证码">复制验证码</button>`
      : `<button type="button" class="acc-v2-twofa-btn" data-account-show-totp-code="${esc(r.id)}" title="显示当前验证码">看验证码</button>`;
    return `<div class="acc-v2-twofa-actions"><span class="acc-v2-twofa-badge"${titleAttr}>已启用</span><button type="button" class="acc-v2-twofa-btn" data-account-copy-secret="totp_secret" data-account-id="${esc(r.id)}" title="复制当前 2FA 密钥">复制密钥</button>${codeHtml}</div>`;
  }
  if (['queued', 'running'].includes(status)) {
    const label = status === 'queued' ? '排队中' : '开启中';
    return `<div class="agent-token-cell"><span class="pill status-running"${titleAttr}>${label}</span></div>`;
  }
  const btnTitle = titleText ? ` title="${esc(titleText)}"` : ` title="为该账号开启 2FA，并把 TOTP 密钥写回账号信息"`;
  const unusable = ['account_deactivated', 'account_deleted', 'account_banned'].includes(errorCode);
  const btn = unusable
    ? `<span class="acc-v2-muted"${btnTitle}>账号不可用</span>`
    : r.has_access_token
      ? `<button type="button" class="acc-v2-twofa-btn" data-account-totp-setup="${esc(r.id)}"${btnTitle}>开启2FA</button>`
      : `<span class="acc-v2-muted">无Token</span>`;
  return `<div class="agent-token-cell">${btn}</div>`;
}
function _codexAgentCell(r) {
  const s = r.codex_agent_status || '';
  const err = r.codex_agent_error || '';
  const msg = r.codex_agent_message || '';
  const title = esc(msg || r.codex_agent_runtime_id || '');
  if (s === 'queued') return `<div class="agent-token-cell"><span class="pill status-running" title="${title}">排队中</span></div>`;
  if (s === 'running') return `<div class="agent-token-cell"><span class="pill status-running" title="${title}">生成中</span></div>`;
  if (s === 'success') {
    const rid = r.codex_agent_runtime_id ? short(r.codex_agent_runtime_id, 10) : '已生成';
    const copy = r.codex_agent_has_token ? `<button data-account-copy-secret="codex_agent_token" data-account-id="${esc(r.id)}" title="按需读取并复制完整 Agent Token">复制Agent Token</button>` : '';
    const download = `<button class="agent-download" data-codex-agent-download="${esc(r.id)}" title="下载该账号 Codex Agent auth.json">下载Agent</button>`;
    const uploadSub2 = `<button class="agent-download" data-codex-agent-upload-sub2="${esc(r.id)}" title="上传该账号 Agent Token 到 sub2api">上传sub2</button>`;
    const regen = `<button data-codex-agent="${esc(r.id)}" title="重新生成 Codex Agent Token">重生成</button>`;
    return `<div class="agent-token-cell"><span class="pill status-success" title="${title}">${esc(rid)}</span>${copy}${download}${uploadSub2}${regen}</div>`;
  }
  const gen = r.has_access_token ? `<button data-codex-agent="${esc(r.id)}" title="生成 Codex CLI agent_identity auth.json/token">生成Agent</button>` : '';
  if (s === 'failed') return `<div class="agent-token-cell"><span class="pill status-failed" title="${title}">失败</span>${gen}</div>`;
  return gen ? `<div class="agent-token-cell">${gen}</div>` : '<span class="muted">-</span>';
}
function _fmtPlanTime(v, dateOnly = false) {
  const formatted = formatDateTime(v);
  return dateOnly ? formatted.slice(0, 10) : formatted.slice(0, 16);
}
function _billingLabel(v) {
  const x = (v || '').toString().toLowerCase();
  if (x === 'monthly') return '月付';
  if (x === 'yearly' || x === 'annual' || x === 'annually') return '年付';
  return v || '';
}
function _discountLabel(r) {
  const amount = r.discount_amount;
  const type = (r.discount_type || '').toString().toLowerCase();
  if (amount === undefined || amount === null || amount === '') return '';
  const n = Number(amount);
  if (type === 'percentage' && !Number.isNaN(n)) { const v = Number.isInteger(n) ? String(n) : String(n); return `${v}%折扣`; }
  if (!Number.isNaN(n)) return `${n}折扣`;
  return `${amount}折扣`;
}
function _promoPlanName(key, campaign) {
  const meta = (campaign && typeof campaign === 'object' && campaign.metadata && typeof campaign.metadata === 'object') ? campaign.metadata : {};
  const raw = String(meta.plan_name || key || '套餐').trim();
  const known = {
    chatgptplusplan:'Plus', chatgptproplan:'Pro', chatgptteamplan:'Team',
    chatgptbusinessplan:'Business', chatgptgoplan:'Go', plus:'Plus', pro:'Pro',
    team:'Team', business:'Business', go:'Go'
  };
  const normalized = raw.toLowerCase().replace(/[\s_-]/g, '');
  if (known[normalized]) return known[normalized];
  return raw.replace(/^chatgpt/i, '').replace(/plan$/i, '') || raw;
}
function _promoDurationLabel(duration) {
  if (!duration || typeof duration !== 'object' || duration.num_periods == null) return '';
  const n = duration.num_periods;
  const period = String(duration.period || '').toLowerCase();
  const unit = period === 'month' ? '个月' : period === 'year' ? '年' : period === 'week' ? '周' : period === 'day' ? '天' : period;
  return `${n}${unit}`;
}
function _promoSummaries(campaigns) {
  if (!campaigns || typeof campaigns !== 'object') return [];
  return Object.entries(campaigns).map(([key, campaign]) => {
    const meta = (campaign && typeof campaign === 'object' && campaign.metadata && typeof campaign.metadata === 'object') ? campaign.metadata : {};
    const percentage = meta.discount && meta.discount.percentage;
    const offer = percentage !== undefined && percentage !== null && percentage !== ''
      ? (Number(percentage) === 100 ? '免费' : `${percentage}%折扣`)
      : String(meta.promotion_type_label || meta.title || (campaign && campaign.id) || '有优惠');
    const duration = _promoDurationLabel(meta.duration);
    return `${_promoPlanName(key, campaign)}：${offer}${duration ? `/${duration}` : ''}`;
  });
}
function _promoStackHtml(baseText, baseClass, summaries, title, detailBtn = '') {
  const base = `<span class="pill ${baseClass}" title="${esc(title)}">${esc(baseText)}</span>`;
  const lines = summaries.map(item => `<span class="pill status-success" title="${esc(item)}">${esc(item)}</span>`).join('');
  return `<div class="plan-promo-stack"><div class="plan-promo-head">${base}${detailBtn}</div>${lines}</div>`;
}
function _planCell(r) {
  const ok = r.plan_check_ok;
  const err = r.plan_check_error || '';
  const plan = (r.current_plan_type || r.plan_type || '-').toString();
  const checked = r.plan_checked_at ? `查询: ${esc(r.plan_checked_at)}` : '未查询实时套餐';
  const route = (r.plan_check_network_route || '').toString();
  const routeLabel = route === 'proxy' ? `网络: 代理${r.plan_check_proxy_used ? ` (${r.plan_check_proxy_used})` : ''}` :
    route === 'direct_fallback' ? `网络: 直连回退${r.plan_check_proxy_fallback_reason ? ` (${r.plan_check_proxy_fallback_reason})` : ''}` :
    route === 'direct' ? '网络: 直连' : '';
  const title = [err ? esc(err) : checked, routeLabel].filter(Boolean).join('；');
  const campaigns = (r.eligible_promo_campaigns && typeof r.eligible_promo_campaigns === 'object') ? r.eligible_promo_campaigns : {};
  const promoSummaries = _promoSummaries(campaigns);
  if (['queued','running'].includes(r.plan_check_status)) {
    const automatic = r.plan_check_trigger === 'registration_auto';
    const queued = r.plan_check_status === 'queued';
    const label = automatic ? (queued ? '自动查询排队中' : '自动查询中') : (queued ? '查询排队中' : '查询中');
    const stamp = queued ? r.plan_check_queued_at : r.plan_check_started_at;
    const detail = stamp ? `${queued ? '入队' : '开始'}: ${esc(formatDateTime(stamp))}` : '';
    return `<span class="pill status-running" title="${detail}">${label}</span>`;
  }
  if (err || r.plan_check_status === 'failed') {
    const reason = err || '套餐查询失败，未返回具体原因';
    const hasLastSuccess = Boolean(r.plan_last_success_at);
    // 套餐列只显示套餐；优惠不进套餐文本（用户要求：优惠不能显示在套餐里）
    const lastPlan = hasLastSuccess ? plan : '';
    const lastText = lastPlan ? `（上次: ${lastPlan}）` : '';
    const lastTitle = hasLastSuccess ? `上次成功: ${esc(formatDateTime(r.plan_last_success_at))}` : '';
    return `<div class="extract-link-cell"><span class="pill status-failed" title="${[esc(reason), lastTitle].filter(Boolean).join('；')}">查询失败${esc(lastText)}</span><div class="extract-link-error" title="${esc(reason)}">${esc(reason)}</div></div>`;
  }
  const lower = plan.toLowerCase();

  if (lower === 'free') {
    const checkedOk = ok === true || r.plan_check_status === 'success';
    if (!checkedOk) {
      return `<span class="pill status-running" title="${title}">${esc(plan)}（待查资格）</span>`;
    }
    const cls = promoSummaries.length ? 'status-success' : 'status-used';
    const t = promoSummaries.length ? `${title}；可用套餐优惠：${promoSummaries.join('；')}` : title;
    const keys = Object.keys(campaigns);
    const detailBtn = keys.length ? `<button type="button" class="promo-detail-btn" data-promo-details="${esc(JSON.stringify(campaigns))}">详情(${keys.length})</button>` : '';
    return _promoStackHtml('free', cls, [], t, detailBtn);
  }

  const cls = 'status-success';
  const expireRaw = r.plan_expires_at || r.expires_at || r.plan_renews_at || r.renews_at || '';
  const expire = _fmtPlanTime(expireRaw, true);
  const parts = [];
  const billing = _billingLabel(r.billing_period);
  if (billing) parts.push(billing);
  if (r.billing_currency) parts.push(r.billing_currency);
  if (expire) parts.push(`到期 ${expire}`);
  const discount = _discountLabel(r);
  if (discount) parts.push(discount);
  const text = parts.length ? `${plan}（${parts.join('/')}）` : plan;
  const detail = [title];
  if (expireRaw) detail.push(`到期时间: ${formatDateTime(expireRaw)}`);
  if (r.plan_renews_at) detail.push(`续费时间: ${formatDateTime(r.plan_renews_at)}`);
  if (r.discount_expires_at) detail.push(`折扣结束: ${formatDateTime(r.discount_expires_at)}`);
  if (r.discount_promo_campaign_id) detail.push(`优惠: ${r.discount_promo_campaign_id}`);
  return _promoStackHtml(text, cls, [], detail.join('；'));
}
function _planAction(r) {
  if (['queued','running'].includes(r.plan_check_status)) {
    const automatic = r.plan_check_trigger === 'registration_auto';
    const queued = r.plan_check_status === 'queued';
    const label = automatic ? (queued ? '自动查询排队中…' : '自动查询中…') : (queued ? '查询排队中…' : '查询中…');
    return `<button data-plan-check="${esc(r.id)}" disabled title="套餐查询正在执行，完成后可再次查询">${label}</button>`;
  }
  return `<button data-plan-check="${esc(r.id)}" title="查询当前套餐；free 账号会检查 Plus 试用资格">查套餐</button>`;
}
function _fmtExtractExpire(v) {
  if (!v) return '';
  const raw = String(v).trim();
  let n = Number(raw);
  if (!Number.isNaN(n) && n > 0) {
    if (n > 1e12) n = Math.floor(n / 1000);
    const d = new Date(n * 1000);
    if (!Number.isNaN(d.getTime())) return _fmtPlanTime(d.toISOString(), false);
  }
  return _fmtPlanTime(raw, false) || raw;
}
function _extractLinkCell(r) {
  const s = r.extract_link_status || '';
  const err = r.extract_link_error || '';
  const msg = r.extract_link_message || '';
  const title = esc(err || msg || r.extract_link_job_id || '');
  if (s === 'queued') return `<span class="pill status-running" title="${title}">提链排队</span>`;
  if (s === 'running') return `<span class="pill status-running" title="${title}">${esc(msg || '提链中')}</span>`;
  if (s === 'success') {
    const typ = (r.extract_link_type || '').toUpperCase();
    const link = r.extract_link_long_url || r.extract_link_copy_paste || '';
    const qr = r.extract_link_image_url_png || r.extract_link_image_url_svg || '';
    const expire = _fmtExtractExpire(r.extract_link_expires_at || '');
    const copy = link ? cbtn('复制提链', link, 'extract-link-btn extract-copy') : '';
    const qrBtn = qr ? `<button class="extract-link-btn extract-qr" data-qr-url="${esc(qr)}" title="打开支付二维码图片">查看二维码</button>` : '';
    const expireHtml = expire ? `<div class="extract-link-expire" title="支付链接过期时间">支付到期：${esc(expire)}</div>` : '';
    return `<div class="extract-link-cell"><span class="pill status-success" title="${title || esc(link)}">提链成功${typ ? '(' + esc(typ) + ')' : ''}</span>${copy}${qrBtn}${expireHtml}</div>`;
  }
  if (s === 'failed') {
    const reason = err || msg || '未知原因';
    return `<div class="extract-link-cell"><span class="pill status-failed" title="${esc(reason)}">提链失败</span><div class="extract-link-error" title="${esc(reason)}">${esc(reason)}</div></div>`;
  }
  return '';
}
function _extractLinkAction(r) {
  const s = r.extract_link_status || '';
  if (['queued','running'].includes(s)) return `<button disabled title="${esc(r.extract_link_message || '提链任务执行中')}">提链中…</button>`;
  const plan = (r.current_plan_type || r.plan_type || '').toString().toLowerCase();
  const eligible = plan === 'free' && !!r.plus_trial_eligible;
  if (!eligible) return '';
  const failedReason = s === 'failed' ? (r.extract_link_error || r.extract_link_message || '') : '';
  return `<button class="good" data-extract-link="${esc(r.id)}" title="${esc(failedReason ? '重新提链；上次失败原因：' + failedReason : '为该 free(可Plus试用) 账号创建 PIX/UPI/KAKAO_PAY/IDEAL 提链任务')}">提链</button>`;
}
function _codexAction(r) {
  const s = r.codex_status || '';
  if (s === 'retrying') {
    return `<button class="danger" data-codex-stop="${esc(r.email)}" title="停止该账号正在进行的 Codex 补跑">停止补跑</button> <button data-codex-reset-retrying="${esc(r.email)}" title="只重置状态；不会向运行线程发送停止信号">重置状态</button>`;
  }
  if (s === 'deactivated') return '';
  const label = s === 'success' ? '重新补跑 Codex' : '补跑 Codex';
  return `<button data-codex-retry="${esc(r.email)}" title="重新跑一次 Codex 授权（会消耗 1 封邮箱 OTP + 1 个接码短信）">${label}</button>`;
}
function _tokenCellV2(r) {
  const live = r.live_check_status || '';
  const liveErr = r.live_check_error || '';
  const liveAt = r.live_checked_at ? formatDateTime(r.live_checked_at) : '';
  let liveHtml = '';
  const liveFp = r.live_check_fingerprint_text || '';
  if (live === 'live') liveHtml = `<div class="acc-v2-sub" title="${esc([liveAt, liveFp].filter(Boolean).join('\n'))}">查活: 正常${liveFp ? ` · ${esc(liveFp)}` : ''}</div>`;
  else if (live === 'queued') liveHtml = `<div class="acc-v2-sub" title="${esc(liveAt)}" style="color:#e6a23c">查活: 排队</div>`;
  else if (live === 'running') liveHtml = `<div class="acc-v2-sub" title="${esc(liveAt)}" style="color:#e6a23c">查活: 运行中</div>`;
  else if (live === 'deactivated') liveHtml = `<div class="acc-v2-sub" title="${esc(liveErr || liveAt)}" style="color:#f56c6c">查活: 已废</div>`;
  else if (live === 'network_unavailable') liveHtml = `<div class="acc-v2-sub" title="${esc(liveErr || liveAt)}" style="color:#e6a23c">查活: 网络异常·未判定</div>`;
  else if (live === 'failed') liveHtml = `<div class="acc-v2-sub" title="${esc(liveErr || liveAt)}" style="color:#e6a23c">查活: 检查失败·未判定</div>`;
  if (r.has_access_token) {
    return `<button type="button" class="acc-v2-token-copy" data-account-copy-secret="access_token" data-account-id="${esc(r.id)}" title="复制完整 Token">复制</button>${liveHtml}`;
  }
  return `<span class="pill status-failed">无Token</span>${liveHtml}`;
}
function _accountsV2MoreMenu(r) {
  const parts = [
    `<button type="button" data-account-copy-secret="copy_line" data-account-id="${esc(r.id)}">复制整行</button>`,
    `<button type="button" data-account-copy-secret="login_credentials" data-account-id="${esc(r.id)}" title="复制邮箱、注册密码和 2FA 密钥">复制登录凭据</button>`,
    `<button type="button" data-account-note="${esc(r.id)}" title="设置/清空该账号备注">备注</button>`,
    `<button type="button" data-account-change-email="${esc(r.id)}" title="从指定邮箱来源领取新邮箱并换绑">换绑邮箱</button>`,
    `<button type="button" data-account-change-email-log="${esc(r.id)}" title="查看最近一次邮箱换绑日志">换绑日志</button>`,
    `<button type="button" onclick="checkSelectedLive([Number('${esc(r.id)}')], this); return false;" title="重新登录该账号，成功则刷新最新 AT/accessToken">查活刷新AT</button>`,
    `<button type="button" onclick="checkSelectedCodexOauth([Number('${esc(r.id)}')], this); return false;" title="只读请求 Codex usage，验证该账号的 OAuth 凭证">Codex OAuth测活</button>`,
    `<button type="button" data-account-live-log="${esc(r.email)}" title="查看该账号最近一次查活日志">查活日志</button>`,
    `<button type="button" data-account-totp-log="${esc(r.email)}" title="查看该账号最近一次 2FA 设置日志">2FA日志</button>`,
    _planAction(r),
    _extractLinkAction(r),
    _codexAction(r),
    `<button type="button" data-codex-log="${esc(r.email)}" title="查看该账号最近一次 Codex 补跑日志">补跑日志</button>`,
    `<button type="button" data-account-archive="${esc(r.id)}" data-archived="${r.archived ? '0' : '1'}" title="${r.archived ? '恢复到默认账号列表' : '归档后默认账号列表不再显示'}">${r.archived ? '恢复' : '归档'}</button>`,
  ].filter(html => String(html || '').trim());
  return parts.join('');
}
function closeAccountsV2MoreMenus(except = null) {
  document.querySelectorAll('.accounts-table-v2 .acc-v2-more.open').forEach(el => {
    if (except && el === except) return;
    el.classList.remove('open');
  });
}
function positionAccountsV2MoreMenu(wrap) {
  const btn = wrap.querySelector('.acc-v2-more-btn');
  const menu = wrap.querySelector('.acc-v2-more-menu');
  if (!btn || !menu) return;
  const rect = btn.getBoundingClientRect();
  const menuW = Math.min(320, window.innerWidth - 16);
  menu.style.width = `${menuW}px`;
  menu.style.maxHeight = `${Math.max(160, window.innerHeight - 16)}px`;
  const menuH = Math.min(menu.scrollHeight || menu.offsetHeight || 0, window.innerHeight - 16);
  let left = rect.right - menuW;
  left = Math.max(8, Math.min(left, window.innerWidth - menuW - 8));
  let top = rect.bottom + 4;
  if (top + menuH > window.innerHeight - 8) top = rect.top - menuH - 4;
  top = Math.max(8, Math.min(top, window.innerHeight - menuH - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}
// 账号表的列定义：把 15 列从一整段模板字面量里拆出来。
// 好处：增删列、调整顺序、将来按列导出 CSV，都只改这张表，不必再动整段 HTML。
const ACCOUNT_COLUMNS_V2 = [
  { cls: 'col-check', cell: (r) => `<input type="checkbox" class="account-row-check" data-account-id="${esc(r.id)}" ${ACCOUNT_SELECTED.has(Number(r.id)) ? 'checked' : ''}>` },
  { cls: 'col-id', cell: (r) => `#${esc(r.id)}` },
  { cls: 'col-email',
    attrs: (r) => `title="${esc(r.email || '-')}${r.original_email ? `\n原邮箱: ${esc(r.original_email)}` : ''}\n${esc(r.user_name || '-')}"`,
    cell: (r) => `<div class="acc-v2-email">${esc(r.email)}${r.archived ? ' <span class="pill status-used" title="该账号已归档">归档</span>' : ''}</div>
        ${r.original_email ? `<div class="acc-v2-email-history">原邮箱: ${esc(r.original_email)}</div>` : ''}
        <div class="acc-v2-email-user">${esc(r.user_name || '-')}</div>
        ${['queued','running'].includes(r.email_change_status) ? '<div class="acc-v2-email-status">换绑中</div>' : (r.email_change_status === 'failed' ? `<div class="acc-v2-email-status" title="${esc(r.email_change_error || '')}">换绑失败: ${esc(r.email_change_error || '-')}</div>` : '')}` },
  { cls: 'col-source', cell: (r) => `${esc(r.email_source || '-')}` },
  { cls: 'col-token', cell: (r) => `${_tokenCellV2(r)}` },
  { cls: 'col-password', cell: (r) => `${r.password ? `<span class="mono" title="${esc(r.password)}">${esc(short(r.password, 18))}</span>` : '<span class="acc-v2-muted">-</span>'}` },
  { cls: 'col-plan', cell: (r) => `${_planCell(r)}<div class="acc-v2-sub">${_extractLinkCell(r)}</div>` },
  { cls: 'col-qual', attrs: (r) => `title="${esc(_qualInfo(r).trialTitle)}"`, cell: (r) => `${_qualInfo(r).trialHtml}` },
  { cls: 'col-qual', attrs: (r) => `title="${esc(_qualInfo(r).promoTitle)}"`, cell: (r) => `${_qualInfo(r).promoHtml}` },
  { cls: 'col-qual', attrs: (r) => `title="${esc(_qualInfo(r).checkTitle)}"`, cell: (r) => `${_qualInfo(r).checkHtml}` },
  { cls: 'col-note', attrs: (r) => `title="${esc(r.note || '')}"`, cell: (r) => `${r.note ? esc(short(r.note, 80)) : '<span class="acc-v2-muted">-</span>'}` },
  { cls: 'col-small', cell: (r) => `${_totpCellV2(r)}` },
  { cls: 'col-status', cell: (r) => `${_codexCellV2(r)}` },
  { cls: 'col-time', attrs: (r) => `title="${esc(formatDateTime(r.created_at))}"`, cell: (r) => `${esc(formatDateTime(r.created_at))}` },
  { cls: 'col-actions', cell: (r) => `<div class="acc-v2-actions">
          <button type="button" class="danger" data-account-delete="${esc(r.id)}" data-email="${esc(r.email)}">删除</button>
          <div class="acc-v2-more">
            <button type="button" class="acc-v2-more-btn" data-acc-more-toggle aria-haspopup="true" aria-expanded="false">更多</button>
            <div class="acc-v2-more-menu" role="menu">${_accountsV2MoreMenu(r)}</div>
          </div>
        </div>` },
];

function renderAccountRowV2(r) {
  return '<tr>' + ACCOUNT_COLUMNS_V2.map((col) => {
    const attrs = typeof col.attrs === 'function' ? ' ' + col.attrs(r) : '';
    return `<td class="${col.cls}"${attrs}>${col.cell(r)}</td>`;
  }).join('') + '</tr>';
}

function renderAccounts() {
  const total = ACCOUNTS_TOTAL;
  const rows = ACCOUNTS;
  const rowHtmlV2 = renderAccountRowV2;
  const bodyV2 = $('#accountsBodyV2');
  if (bodyV2) {
    bodyV2.innerHTML = rows.map(rowHtmlV2).join('') || '<tr><td colspan="12" style="text-align:center;color:#909399;padding:28px;">暂无账号</td></tr>';
  }
  updateAccountSelectionUi(rows);
  _renderPager('accounts', total);
}

function updateAccountSelectionUi(pageRows = null) {
  const none = ACCOUNT_SELECTED.size === 0;
  const hintV2 = $('#accountsSelectedHintV2');
  if (hintV2) hintV2.textContent = `已选 ${ACCOUNT_SELECTED.size}`;

  const archiveLabel = SHOW_ARCHIVED_ACCOUNTS ? '恢复选中' : '归档选中';
  const archiveTitle = SHOW_ARCHIVED_ACCOUNTS ? '把选中的归档账号恢复到默认账号列表' : '归档选中的账号；默认账号列表将不再查询/显示这些账号';
  const v2Ids = [
    'btnCheckSelectedPlansV2', 'btnExtractSelectedLinksV2',
    'btnCheckSelectedCodexOauthV2', 'btnRetrySelectedCodexV2', 'btnDownloadSelectedCpaV2', 'btnStopSelectedCodexV2',
    'btnSetupSelectedTotpV2', 'btnChangeSelectedEmailsV2', 'btnNoteSelectedAccountsV2', 'btnCopySelectedLinesV2', 'btnCopySelectedTokensV2', 'btnCopySelectedEmailsV2',
    'btnDownloadSelectedTxtV2', 'btnDownloadSelectedFullExportV2', 'btnCopySelectedFullExportV2', 'btnCopySelectedLoginCredentialsV2', 'btnArchiveSelectedAccountsV2', 'btnDeleteSelectedAccountsV2',
  ];
  v2Ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = none;
    if (id === 'btnArchiveSelectedAccountsV2') {
      el.textContent = archiveLabel;
      el.title = archiveTitle;
    }
  });

  if (!pageRows) pageRows = ACCOUNTS;
  const pageIds = pageRows.map(r => Number(r.id));
  const checkedCount = pageIds.filter(id => ACCOUNT_SELECTED.has(id)).length;
  const cbAll = document.getElementById('accountsSelectAllV2');
  if (cbAll) {
    cbAll.checked = pageIds.length > 0 && checkedCount === pageIds.length;
    cbAll.indeterminate = checkedCount > 0 && checkedCount < pageIds.length;
    cbAll.disabled = pageIds.length === 0;
  }
}

// ---------- 补跑日志面板 ----------
let retryLogEmail = null, retryLogTimer = null;

function openRetryLog(email) {
  retryLogEmail = email;
  $('#retryLogEmail').textContent = email;
  $('#retryLogPanel').classList.remove('hidden');
  updateModalScrollLock();
  $('#retryLogContent').textContent = '加载中…';
  pollRetryLog();
  clearInterval(retryLogTimer);
  retryLogTimer = setInterval(pollRetryLog, 5000);
}
async function pollRetryLog() {
  if (!retryLogEmail) return;
  if (document.visibilityState === 'hidden') return;
  try {
    const r = await api(`/api/codex/retry-log?email=${encodeURIComponent(retryLogEmail)}`);
    const c = $('#retryLogContent');
    const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
    c.textContent = r.log || '(暂无日志，等待任务写入…)';
    if (atBottom) c.scrollTop = c.scrollHeight;
    if (!r.running) {
      clearInterval(retryLogTimer);
      loadAccounts();
    }
  } catch(e) {}
}
$('#btnCloseRetryLog').addEventListener('click', closeRetryLogModal);

// ---------- 查活日志面板 ----------
let liveLogEmail = null, liveLogTimer = null;

function openLiveLog(email) {
  liveLogEmail = email;
  $('#liveLogEmail').textContent = email;
  $('#liveLogPanel').classList.remove('hidden');
  updateModalScrollLock();
  $('#liveLogContent').textContent = '加载中…';
  pollLiveLog();
  clearInterval(liveLogTimer);
  liveLogTimer = setInterval(pollLiveLog, 5000);
}
async function pollLiveLog() {
  if (!liveLogEmail) return;
  if (document.visibilityState === 'hidden') return;
  try {
    const r = await api(`/api/accounts/live-check-log?email=${encodeURIComponent(liveLogEmail)}`);
    const c = $('#liveLogContent');
    const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
    c.textContent = r.log || '(暂无日志，等待任务写入…)';
    if (atBottom) c.scrollTop = c.scrollHeight;
    if (!r.running) {
      clearInterval(liveLogTimer);
      loadAccounts();
    }
  } catch(e) {}
}
$('#btnCloseLiveLog').addEventListener('click', closeLiveLogModal);

// ---------- 2FA日志面板 ----------
let totpLogEmail = null, totpLogTimer = null;

function openTotpLog(email) {
  totpLogEmail = email;
  $('#totpLogEmail').textContent = email;
  $('#totpLogPanel').classList.remove('hidden');
  updateModalScrollLock();
  $('#totpLogContent').textContent = '加载中…';
  pollTotpLog();
  clearInterval(totpLogTimer);
  totpLogTimer = setInterval(pollTotpLog, 5000);
}
async function pollTotpLog() {
  if (!totpLogEmail) return;
  if (document.visibilityState === 'hidden') return;
  try {
    const r = await api(`/api/accounts/totp-setup-log?email=${encodeURIComponent(totpLogEmail)}`);
    const c = $('#totpLogContent');
    const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
    c.textContent = r.log || '(暂无日志，等待任务写入…)';
    if (atBottom) c.scrollTop = c.scrollHeight;
    if (!r.running) {
      clearInterval(totpLogTimer);
      loadAccounts();
    }
  } catch(e) {}
}
function closeTotpLogModal() {
  $('#totpLogPanel').classList.add('hidden');
  updateModalScrollLock();
  clearInterval(totpLogTimer);
  totpLogTimer = null;
  totpLogEmail = null;
}
$('#btnCloseTotpLog').addEventListener('click', closeTotpLogModal);

let emailChangeLogAccountId = null, emailChangeLogTimer = null;
function openEmailChangeLog(accountId) {
  emailChangeLogAccountId = Number(accountId);
  $('#emailChangeLogAccount').textContent = `账号 #${emailChangeLogAccountId}`;
  $('#emailChangeLogPanel').classList.remove('hidden');
  $('#emailChangeLogContent').textContent = '加载中…';
  updateModalScrollLock();
  pollEmailChangeLog();
  clearInterval(emailChangeLogTimer);
  emailChangeLogTimer = setInterval(pollEmailChangeLog, 3000);
}
async function pollEmailChangeLog() {
  if (!emailChangeLogAccountId) return;
  if (document.visibilityState === 'hidden') return;
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(emailChangeLogAccountId)}/change-email-log`);
    const content = $('#emailChangeLogContent');
    const atBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 30;
    content.textContent = r.log || '(暂无换绑日志)';
    $('#emailChangeLogAccount').textContent = `账号 #${emailChangeLogAccountId}${r.email ? ' · ' + r.email : ''}`;
    if (atBottom) content.scrollTop = content.scrollHeight;
    if (!r.running) clearInterval(emailChangeLogTimer);
  } catch(e) {}
}
function closeEmailChangeLog() {
  $('#emailChangeLogPanel').classList.add('hidden');
  updateModalScrollLock();
  clearInterval(emailChangeLogTimer);
  emailChangeLogTimer = null;
  emailChangeLogAccountId = null;
}
$('#btnCloseEmailChangeLog').addEventListener('click', closeEmailChangeLog);

// 账号操作按钮（事件委托）
async function onAccountsBodyClick(e) {
  const moreToggle = e.target.closest('[data-acc-more-toggle]');
  if (moreToggle) {
    e.preventDefault();
    e.stopPropagation();
    const wrap = moreToggle.closest('.acc-v2-more');
    if (!wrap) return;
    const willOpen = !wrap.classList.contains('open');
    closeAccountsV2MoreMenus();
    if (willOpen) {
      wrap.classList.add('open');
      moreToggle.setAttribute('aria-expanded', 'true');
      positionAccountsV2MoreMenu(wrap);
    }
    return;
  }
  if (e.target.closest('.acc-v2-more-menu')) {
    // 点菜单项后收起，再继续走下面的动作处理
    closeAccountsV2MoreMenus();
  }

  const copySecretBtn = e.target.closest('[data-account-copy-secret]');
  if (copySecretBtn) {
    const id = Number(copySecretBtn.dataset.accountId);
    const field = copySecretBtn.dataset.accountCopySecret;
    copySecretBtn.disabled = true;
    try {
      const value = await fetchOneAccountSecret(id, field);
      if (!value) { showToast('可复制内容为空'); return; }
      copyText(value);
      showToast(
        field === 'access_token' ? 'Token 已复制' :
        field === 'codex_agent_token' ? 'Agent Token 已复制' :
        field === 'login_credentials' ? '邮箱、注册密码和 2FA 密钥已复制' :
        field === 'totp_secret' ? '2FA 密钥已复制' :
        field === 'totp_code' ? `验证码已复制：${value}` :
        '账号整行已复制'
      );
    } catch(err) {
      showToast('复制失败: ' + err.message);
    } finally {
      copySecretBtn.disabled = false;
    }
    return;
  }

  const showTotpBtn = e.target.closest('[data-account-show-totp-code]');
  if (showTotpBtn) {
    await showAccountTotpCode(Number(showTotpBtn.dataset.accountShowTotpCode), showTotpBtn);
    return;
  }

  const copyShownTotpBtn = e.target.closest('[data-account-copy-shown-totp-code]');
  if (copyShownTotpBtn) {
    const id = Number(copyShownTotpBtn.dataset.accountCopyShownTotpCode);
    const cached = TOTP_CODE_CACHE.get(id);
    if (!cached?.code) { showToast('请先点“看验证码”'); return; }
    copyText(cached.code);
    showToast(`验证码已复制：${cached.code}`);
    return;
  }

  const planBtn = e.target.closest('[data-plan-check]');
  if (planBtn) {
    await checkOnePlan(Number(planBtn.dataset.planCheck), planBtn);
    return;
  }

  const liveBtn = e.target.closest('[data-account-live-check]');
  if (liveBtn) {
    await checkSelectedLive([Number(liveBtn.dataset.accountLiveCheck)], liveBtn);
    return;
  }

  const liveLogBtn = e.target.closest('[data-account-live-log]');
  if (liveLogBtn) {
    openLiveLog(liveLogBtn.dataset.accountLiveLog);
    return;
  }

  const totpLogBtn = e.target.closest('[data-account-totp-log]');
  if (totpLogBtn) {
    openTotpLog(totpLogBtn.dataset.accountTotpLog);
    return;
  }

  const delBtn = e.target.closest('[data-account-delete]');
  if (delBtn) {
    await deleteAccount(Number(delBtn.dataset.accountDelete), delBtn.dataset.email, delBtn);
    return;
  }

  const archiveBtn = e.target.closest('[data-account-archive]');
  if (archiveBtn) {
    await archiveOneAccount(Number(archiveBtn.dataset.accountArchive), archiveBtn.dataset.archived === '1', archiveBtn);
    return;
  }

  const noteBtn = e.target.closest('[data-account-note]');
  if (noteBtn) {
    await editAccountNote(Number(noteBtn.dataset.accountNote), noteBtn);
    return;
  }

  const changeEmailBtn = e.target.closest('[data-account-change-email]');
  if (changeEmailBtn) {
    openEmailChangeModal([Number(changeEmailBtn.dataset.accountChangeEmail)]);
    return;
  }

  const changeEmailLogBtn = e.target.closest('[data-account-change-email-log]');
  if (changeEmailLogBtn) {
    openEmailChangeLog(Number(changeEmailLogBtn.dataset.accountChangeEmailLog));
    return;
  }

  const extractBtn = e.target.closest('[data-extract-link]');
  if (extractBtn) {
    await extractOneLink(Number(extractBtn.dataset.extractLink), extractBtn);
    return;
  }

  const totpBtn = e.target.closest('[data-account-totp-setup]');
  if (totpBtn) {
    await setupAccountTotp(Number(totpBtn.dataset.accountTotpSetup), totpBtn);
    return;
  }

  const agentBtn = e.target.closest('[data-codex-agent]');
  if (agentBtn) {
    await generateOneCodexAgent(Number(agentBtn.dataset.codexAgent), agentBtn);
    return;
  }

  const agentDownloadBtn = e.target.closest('[data-codex-agent-download]');
  if (agentDownloadBtn) {
    downloadOneCodexAgent(Number(agentDownloadBtn.dataset.codexAgentDownload));
    return;
  }

  const agentUploadSub2Btn = e.target.closest('[data-codex-agent-upload-sub2]');
  if (agentUploadSub2Btn) {
    await uploadOneCodexAgentSub2(Number(agentUploadSub2Btn.dataset.codexAgentUploadSub2), agentUploadSub2Btn);
    return;
  }

  const qrBtn = e.target.closest('[data-qr-url]');
  if (qrBtn) {
    const url = qrBtn.dataset.qrUrl || '';
    if (!url) { showToast('二维码地址为空'); return; }
    openQrModal(url);
    return;
  }

  const logBtn = e.target.closest('[data-codex-log]');
  if (logBtn) {
    openRetryLog(logBtn.dataset.codexLog);
    return;
  }

  const stopCodexBtn = e.target.closest('[data-codex-stop]');
  if (stopCodexBtn) {
    const email = stopCodexBtn.dataset.codexStop;
    if (!uiConfirmSync(`确定停止该账号的 Codex 补跑吗？\n\n${email}\n\n会发送停止信号并将状态标记为“已停止”。`)) return;
    stopCodexBtn.disabled = true;
    try {
      const r = await api('/api/codex/stop', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email}) });
      showToast(r.message || '已发送停止信号');
      if (retryLogEmail === email) pollRetryLog();
      loadAccounts();
    } catch(err) {
      showToast('停止失败: ' + err.message);
      stopCodexBtn.disabled = false;
    }
    return;
  }

  const resetBtn = e.target.closest('[data-codex-reset-retrying]');
  if (resetBtn) {
    const email = resetBtn.dataset.codexResetRetrying;
    if (!uiConfirmSync(`确定重置该账号的 Codex「补跑中」状态吗？\n\n${email}\n\n重置后状态会变为“失败”，可再次点击“补跑 Codex”。`)) return;
    resetBtn.disabled = true;
    try {
      const r = await api('/api/codex/reset-retrying', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, status:'failed'}) });
      showToast(r.message || '已重置补跑状态');
      if (retryLogEmail === email) {
        clearInterval(retryLogTimer);
        pollRetryLog();
      }
      loadAccounts();
    } catch(err) {
      showToast('重置失败: ' + err.message);
      resetBtn.disabled = false;
    }
    return;
  }

  const btn = e.target.closest('[data-codex-retry]');
  if (!btn) return;
  const email = btn.dataset.codexRetry;
  if (!uiConfirmSync(`重新跑 Codex 授权？\n\n${email}\n\n将消耗：\n  • 1 封邮箱 OTP（自动收）\n  • 1 个接码短信（价格按当前 SMSBower 配置，实际以供应商返回为准）\n\n补跑会在后台进行（~1-2 分钟），完成后状态自动更新。`)) return;
  btn.disabled = true;
  try {
    const r = await api('/api/codex/retry', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email}) });
    showToast(r.message || '已开始补跑');
    loadAccounts();
    openRetryLog(email);
    const poll = setInterval(async () => {
      const acc = ACCOUNTS.find(a => a.email === email);
      if (!acc || acc.codex_status !== 'retrying') clearInterval(poll);
    }, 15000);
    setTimeout(() => clearInterval(poll), 5 * 60 * 1000);
  } catch(err) {
    showToast('触发补跑失败: ' + err.message);
    btn.disabled = false;
  }
}
function onAccountsBodyChange(e) {
  const cb = e.target.closest('.account-row-check');
  if (!cb) return;
  const id = Number(cb.dataset.accountId);
  if (cb.checked) ACCOUNT_SELECTED.add(id);
  else ACCOUNT_SELECTED.delete(id);
  updateAccountSelectionUi();
}
function syncAccountsSelectAll(checked) {
  const pageRows = ACCOUNTS;
  if (checked) pageRows.forEach(r => ACCOUNT_SELECTED.add(Number(r.id)));
  else pageRows.forEach(r => ACCOUNT_SELECTED.delete(Number(r.id)));
  renderAccounts();
}
(function bindAccountsV2Events() {
  const bodyV2 = $('#accountsBodyV2');
  if (bodyV2) {
    bodyV2.addEventListener('click', onAccountsBodyClick);
    bodyV2.addEventListener('change', onAccountsBodyChange);
  }
  const selectAllV2 = $('#accountsSelectAllV2');
  if (selectAllV2) selectAllV2.addEventListener('change', (e) => syncAccountsSelectAll(e.target.checked));
  document.addEventListener('click', (e) => {
    if (e.target.closest('.acc-v2-more')) return;
    closeAccountsV2MoreMenus();
  });
  window.addEventListener('scroll', () => closeAccountsV2MoreMenus(), true);
  // 清理已废：用事件委托绑定，避免按钮晚于初始化渲染导致监听丢失。
  document.addEventListener('click', (e) => {
    const cleanupBtn = e.target.closest && e.target.closest('#btnCleanupDeadV2');
    if (!cleanupBtn || cleanupBtn.disabled) return;
    e.preventDefault();
    cleanupDeadAccounts();
  });
  window.addEventListener('resize', () => closeAccountsV2MoreMenus());
})();
function setAccountsFilterToggle(el, on) {
  if (!el) return;
  el.classList.toggle('is-active', !!on);
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
}
async function applyAccountsArchivedFilter(on) {
  SHOW_ARCHIVED_ACCOUNTS = !!on;
  setAccountsFilterToggle($('#showArchivedAccountsV2'), SHOW_ARCHIVED_ACCOUNTS);
  ACCOUNT_SELECTED.clear();
  PAGERS.accounts.page = 1;
  await loadAccounts();
  await pollAccountPlanStatuses();
}
async function applyAccountsPlusFilter(on) {
  SHOW_PLUS_ACCOUNTS_ONLY = !!on;
  if (SHOW_PLUS_ACCOUNTS_ONLY) {
    SHOW_PLUS_TRIAL_ACCOUNTS_ONLY = false;
    SHOW_FREE_ACCOUNTS_ONLY = false;
  }
  setAccountsFilterToggle($('#showPlusAccountsOnlyV2'), SHOW_PLUS_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showPlusTrialAccountsOnlyV2'), SHOW_PLUS_TRIAL_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showFreeAccountsOnlyV2'), SHOW_FREE_ACCOUNTS_ONLY);
  ACCOUNT_SELECTED.clear();
  PAGERS.accounts.page = 1;
  await loadAccounts();
  await pollAccountPlanStatuses();
}
async function applyAccountsPlusTrialFilter(on) {
  SHOW_PLUS_TRIAL_ACCOUNTS_ONLY = !!on;
  if (SHOW_PLUS_TRIAL_ACCOUNTS_ONLY) {
    SHOW_PLUS_ACCOUNTS_ONLY = false;
    SHOW_FREE_ACCOUNTS_ONLY = false;
  }
  setAccountsFilterToggle($('#showPlusAccountsOnlyV2'), SHOW_PLUS_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showPlusTrialAccountsOnlyV2'), SHOW_PLUS_TRIAL_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showFreeAccountsOnlyV2'), SHOW_FREE_ACCOUNTS_ONLY);
  ACCOUNT_SELECTED.clear();
  PAGERS.accounts.page = 1;
  await loadAccounts();
  await pollAccountPlanStatuses();
}
async function applyAccountsFreeFilter(on) {
  SHOW_FREE_ACCOUNTS_ONLY = !!on;
  if (SHOW_FREE_ACCOUNTS_ONLY) {
    SHOW_PLUS_ACCOUNTS_ONLY = false;
    SHOW_PLUS_TRIAL_ACCOUNTS_ONLY = false;
  }
  setAccountsFilterToggle($('#showPlusAccountsOnlyV2'), SHOW_PLUS_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showPlusTrialAccountsOnlyV2'), SHOW_PLUS_TRIAL_ACCOUNTS_ONLY);
  setAccountsFilterToggle($('#showFreeAccountsOnlyV2'), SHOW_FREE_ACCOUNTS_ONLY);
  ACCOUNT_SELECTED.clear();
  PAGERS.accounts.page = 1;
  await loadAccounts();
  await pollAccountPlanStatuses();
}
async function refreshAccountsList(btn) {
  if (!btn) return;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '刷新中…';
  try {
    planStatusRevision = '';
    await loadAccounts();
    await pollAccountPlanStatuses();
    loadSummary();
    showToast('账号列表已刷新');
  } catch(e) {
    showToast('刷新账号失败: ' + e.message);
  } finally {
    btn.textContent = old;
    btn.disabled = false;
  }
}
(function bindAccountsFilterV2() {
  const qV2 = $('#qAccountsV2');
  if (qV2) qV2.addEventListener('input', debounce(() => {
    ACCOUNT_SELECTED.clear();
    PAGERS.accounts.page = 1;
    loadAccounts();
  }, 250));
  const codexV2 = $('#codexStatusFilterV2');
  if (codexV2) codexV2.addEventListener('change', () => {
    ACCOUNT_SELECTED.clear();
    PAGERS.accounts.page = 1;
    loadAccounts();
  });
  const totpV2 = $('#totpStatusFilterV2');
  if (totpV2) totpV2.addEventListener('change', () => {
    ACCOUNT_SELECTED.clear();
    PAGERS.accounts.page = 1;
    loadAccounts();
  });

  const archivedV2 = $('#showArchivedAccountsV2');
  if (archivedV2) archivedV2.addEventListener('click', () => {
    applyAccountsArchivedFilter(!SHOW_ARCHIVED_ACCOUNTS);
  });
  const plusV2 = $('#showPlusAccountsOnlyV2');
  if (plusV2) plusV2.addEventListener('click', () => {
    applyAccountsPlusFilter(!SHOW_PLUS_ACCOUNTS_ONLY);
  });
  const plusTrialV2 = $('#showPlusTrialAccountsOnlyV2');
  if (plusTrialV2) plusTrialV2.addEventListener('click', () => {
    applyAccountsPlusTrialFilter(!SHOW_PLUS_TRIAL_ACCOUNTS_ONLY);
  });
  const promoTypeV2 = $('#promoTypeFilterV2');
  if (promoTypeV2) promoTypeV2.addEventListener('change', () => {
    applyAccountsPlusTrialFilter(true);
  });
  const promoDiscountV2 = $('#promoDiscountFilterV2');
  if (promoDiscountV2) promoDiscountV2.addEventListener('change', () => {
    applyAccountsPlusTrialFilter(true);
  });
  const freeV2 = $('#showFreeAccountsOnlyV2');
  if (freeV2) freeV2.addEventListener('click', () => {
    applyAccountsFreeFilter(!SHOW_FREE_ACCOUNTS_ONLY);
  });
  const refreshV2 = $('#btnRefreshAccountsV2');
  if (refreshV2) refreshV2.addEventListener('click', () => refreshAccountsList(refreshV2));

  bindDateFilterPanel({
    btnId: 'btnDateFilterAccountsV2',
    panelId: 'dateFilterPanelAccountsV2',
    fromId: 'dateFromAccountsV2',
    toId: 'dateToAccountsV2',
    onApply: () => { ACCOUNT_SELECTED.clear(); PAGERS.accounts.page = 1; loadAccounts(); },
  });
})();


async function fetchAccountSecrets(ids, field) {
  const r = await api('/api/accounts/secret-bulk', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({account_ids: ids, field}),
  });
  return (r.values || []).map(x => x.value).filter(Boolean);
}

async function fetchOneAccountSecret(id, field) {
  const r = await api(`/api/accounts/${encodeURIComponent(id)}/secret?field=${encodeURIComponent(field)}`);
  return r.value || '';
}

async function fetchAccountTotpCode(id) {
  const r = await api(`/api/accounts/${encodeURIComponent(id)}/secret?field=totp_code`);
  return r.value || '';
}

async function showAccountTotpCode(id, btn) {
  if (btn) btn.disabled = true;
  try {
    const code = await fetchAccountTotpCode(id);
    if (!code) { showToast('当前验证码为空'); return; }
    TOTP_CODE_CACHE.set(Number(id), { code, at: new Date().toLocaleString() });
    renderAccounts();
    showToast(`验证码：${code}`);
  } catch (err) {
    showToast('获取验证码失败: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function setupAccountTotp(id, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  const email = acc?.email || '';
  if (!uiConfirmSync(`确定为该账号开启 2FA？\n\n${email}\n\n完成后会把 TOTP 密钥写回账号信息。`)) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(id)}/totp-setup`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({}),
    });
    showToast(r.message || '2FA 已开始开启');
    planStatusRevision = '';
    await loadAccounts();
    await pollAccountPlanStatuses();
  } catch (err) {
    showToast('开启 2FA 失败: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function setupSelectedTotp() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  if (!uiConfirmSync(`确定为选中的 ${ids.length} 个账号批量开启 2FA？\n\n任务会在后台排队执行，完成后自动把 TOTP 密钥写回账号信息。\n已经开启、没有 Token 或正在设置中的账号会自动跳过。`)) return;

  const btn = document.getElementById('btnSetupSelectedTotpV2');
  if (!btn) return;
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '入队中…';
  try {
    const r = await api('/api/accounts/totp-setup-bulk', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    (r.started || []).forEach(item => ACCOUNT_SELECTED.delete(Number(item.id)));
    const skippedCount = Number(r.skipped_count ?? (r.skipped || []).length) || 0;
    const busyCount = Number(r.busy_count ?? (r.busy || []).length) || 0;
    const failedCount = Number(r.failed_count ?? (r.failed || []).length) || 0;
    const details = [];
    if (skippedCount) details.push(`跳过 ${skippedCount} 个`);
    if (busyCount) details.push(`进行中 ${busyCount} 个`);
    if (failedCount) details.push(`失败 ${failedCount} 个`);
    showToast(`已入队 ${r.started_count || 0} 个${details.length ? '，' + details.join('，') : ''}`);
    planStatusRevision = '';
    await loadAccounts();
    await pollAccountPlanStatuses();
  } catch (err) {
    showToast('批量开启 2FA 失败: ' + err.message);
  } finally {
    btn.textContent = oldText;
    updateAccountSelectionUi();
  }
}

async function copySelectedAccountLines() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  try {
    const lines = await fetchAccountSecrets(ids, 'copy_line');
    if (!lines.length) { showToast('选中账号没有可复制整行'); return; }
    copyText(lines.join('\n'));
    showToast(`已复制 ${lines.length} 行`);
  } catch(err) { showToast('复制失败: ' + err.message); }
}

async function copySelectedAccountTokens() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  try {
    const lines = await fetchAccountSecrets(ids, 'access_token');
    if (!lines.length) { showToast('选中账号没有可复制 Token'); return; }
    copyText(lines.join('\n'));
    showToast(`已复制 ${lines.length} 个 Token`);
  } catch(err) { showToast('复制失败: ' + err.message); }
}

function bindDateFilterPanel({ btnId, panelId, fromId, toId, onApply }) {
  const btn = document.getElementById(btnId);
  const panel = document.getElementById(panelId);
  if (!btn || !panel) return;
  const wrap = panel.closest('.acc-v2-date-select');
  const now = new Date();
  let viewY = now.getFullYear();
  let viewM = now.getMonth();
  let start = document.getElementById(fromId)?.value || '';
  let end = document.getElementById(toId)?.value || '';
  let openSnapshot = { start, end };
  let dirty = false;
  if (start) { const d = new Date(start); viewY = d.getFullYear(); viewM = d.getMonth(); }
  const fmt = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const setHidden = () => {
    const f = document.getElementById(fromId); if (f) f.value = start;
    const t = document.getElementById(toId); if (t) t.value = end;
  };
  const syncBtnText = () => {
    btn.textContent = (start || end) ? `📅 ${start || '…'} ~ ${end || '…'}` : '📅 日期筛选';
  };
  const close = () => {
    if (wrap) wrap.classList.remove('open');
    else panel.classList.remove('open');
    // 只选了开始/结束其中一端就关闭 → 还原到打开前的完整状态，避免留下残缺筛选
    if (dirty && !(start && end)) {
      start = openSnapshot.start;
      end = openSnapshot.end;
      setHidden();
      syncBtnText();
    }
    dirty = false;
  };

  const render = () => {
    const y = viewY, m = viewM;
    const firstDay = new Date(y, m, 1);
    const dim = new Date(y, m + 1, 0).getDate();
    const lead = firstDay.getDay();
    const cells = [];
    for (let i = lead - 1; i >= 0; i--) cells.push({ d: new Date(y, m, -i), other: true });
    for (let day = 1; day <= dim; day++) cells.push({ d: new Date(y, m, day), other: false });
    let tail = 1;
    while (cells.length % 7 !== 0) cells.push({ d: new Date(y, m + 1, tail++), other: true });
    panel.innerHTML = `
      <div class="cal">
        <div class="cal-head">
          <button type="button" class="cal-nav" data-cal-nav="-1" title="上个月">‹</button>
          <span class="cal-title">${y}年${m + 1}月</span>
          <button type="button" class="cal-nav" data-cal-nav="1" title="下个月">›</button>
        </div>
        <div class="cal-week">${['日', '一', '二', '三', '四', '五', '六'].map(w => `<span>${w}</span>`).join('')}</div>
        <div class="cal-grid">
          ${cells.map(({ d, other }) => {
            const ds = fmt(d);
            const cls = ['cal-cell'];
            if (other) cls.push('other');
            if (start && end && ds > start && ds < end) cls.push('in-range');
            if (ds === start || ds === end) cls.push(ds === start && ds === end ? 'start end' : (ds === start ? 'start' : 'end'));
            return `<button type="button" class="${cls.join(' ')}" data-date="${ds}" data-other="${other ? 1 : 0}">${d.getDate()}</button>`;
          }).join('')}
        </div>
        <div class="cal-foot">
          <div class="cal-foot-btns">
            <button type="button" class="jobs-tb-btn" data-quick-date="today">今天</button>
            <button type="button" class="jobs-tb-btn" data-quick-date="yesterday">昨天</button>
            <button type="button" class="jobs-tb-btn" data-quick-date="3d">近3天</button>
            <button type="button" class="jobs-tb-btn" data-quick-date="7d">近7天</button>
          </div>
          <div class="cal-foot-tip">双击某个日期，只看当天</div>
        </div>
      </div>`;
    panel.querySelectorAll('[data-cal-nav]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation();
      viewM += Number(b.dataset.calNav);
      if (viewM < 0) { viewM = 11; viewY--; }
      if (viewM > 11) { viewM = 0; viewY++; }
      render();
    }));
    panel.querySelectorAll('[data-date]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation();
      const ds = b.dataset.date;
      if (b.dataset.other === '1') {
        const d = new Date(ds);
        viewY = d.getFullYear(); viewM = d.getMonth();
      }
      dirty = true;
      if (!start || (start && end)) { start = ds; end = ''; }
      else { end = ds; if (end < start) [start, end] = [end, start]; }
      setHidden();
      syncBtnText();
      if (start && end) { close(); if (onApply) onApply(); }
      else render();
    }));
    panel.querySelectorAll('[data-date]').forEach(b => b.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      start = b.dataset.date;
      end = start;
      dirty = false;
      setHidden();
      syncBtnText();
      close();
      if (onApply) onApply();
    }));
    panel.querySelectorAll('[data-quick-date]').forEach(b => b.addEventListener('click', (e) => {
      e.stopPropagation();
      const en = new Date();
      const st = new Date();
      const k = b.dataset.quickDate;
      if (k === 'yesterday') { st.setDate(st.getDate() - 1); en.setDate(en.getDate() - 1); }
      else if (k === '3d') st.setDate(st.getDate() - 2);
      else if (k === '7d') st.setDate(st.getDate() - 6);
      start = fmt(st); end = fmt(en);
      dirty = false;
      setHidden(); syncBtnText(); close(); if (onApply) onApply();
    }));
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = wrap ? !wrap.classList.contains('open') : !panel.classList.contains('open');
    close();
    if (willOpen) {
      openSnapshot = { start, end };
      dirty = false;
      if (wrap) wrap.classList.add('open');
      render();
    }
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest(`#${btnId}, #${panelId}`)) return;
    close();
  });
  syncBtnText();
}

function copySelectedAccountEmails() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  const emails = ACCOUNTS
    .filter(r => ids.includes(Number(r.id)))
    .map(r => (r.email || '').trim())
    .filter(Boolean);
  if (!emails.length) { showToast('选中账号没有可复制的邮箱'); return; }
  copyText(emails.join('\n'));
  showToast(`已复制 ${emails.length} 个邮箱`);
}

async function downloadSelectedAccountTxt() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  let lines = [];
  try {
    lines = await fetchAccountSecrets(ids, 'copy_line');
  } catch(err) { showToast('生成 TXT 失败: ' + err.message); return; }
  if (!lines.length) { showToast('选中账号没有可下载整行'); return; }
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const filename = `accounts-selected-${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.txt`;
  const blob = new Blob(['\ufeff' + lines.join('\n') + '\n'], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
  showToast(`已下载 ${lines.length} 个账号 TXT`);
}

async function downloadSelectedAccountFullExport() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  let lines = [];
  try {
    lines = await fetchAccountSecrets(ids, 'full_export');
  } catch(err) { showToast('生成完整导出失败: ' + err.message); return; }
  if (!lines.length) { showToast('选中账号没有可导出内容'); return; }
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const filename = `accounts-full-export-${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.txt`;
  const blob = new Blob(['\ufeff' + lines.join('\n') + '\n'], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
  showToast(`已导出 ${lines.length} 个账号`);
}

async function copySelectedAccountFullExport() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  let lines = [];
  try {
    lines = await fetchAccountSecrets(ids, 'full_export');
  } catch(err) { showToast('复制完整导出失败: ' + err.message); return; }
  if (!lines.length) { showToast('选中账号没有可复制内容'); return; }
  copyText(lines.join('\n'));
  showToast(`已复制 ${lines.length} 个账号完整导出`);
}

async function copySelectedAccountLoginCredentials() {
  const ids = Array.from(ACCOUNT_SELECTED).map(Number);
  if (!ids.length) { showToast('请先选择账号'); return; }
  let lines = [];
  try {
    lines = await fetchAccountSecrets(ids, 'login_credentials');
  } catch(err) { showToast('复制登录凭据失败: ' + err.message); return; }
  if (!lines.length) { showToast('选中账号没有可复制的登录凭据'); return; }
  copyText(lines.join('\n'));
  showToast(`已复制 ${lines.length} 个账号登录凭据`);
}

async function downloadSelectedCpa() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selectedAccounts = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const missingCodex = selectedAccounts.filter(a => (a.codex_status || '') !== 'success').length;
  let msg = `确定从 CPA 下载选中的 ${ids.length} 个账号的 CPA/Codex JSON 吗？\n\n会按账号邮箱匹配 CPA auth-files，成功的文件会打包成 ZIP。`;
  if (missingCodex) msg += `\n\n其中 ${missingCodex} 个账号本地 Codex 状态不是 success，若 CPA 端没有文件会写入 manifest 错误清单。`;
  if (!uiConfirmSync(msg)) return;
  const btn = $('#btnDownloadSelectedCpaV2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '下载中…';
  try {
    // 先由服务端准备 ZIP，再用同源 GET 顶层下载。
    // 这样比隐藏 iframe / blob URL 更像普通用户点击下载，Chrome 不容易挂起或标记“不安全下载”。
    const r = await api('/api/accounts/download-cpa-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, prepare: true}),
    });
    if (!r.download_url) throw new Error('服务端未返回下载地址');
    const a = document.createElement('a');
    a.href = r.download_url;
    a.download = r.filename || '';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 1000);
    showToast(`已生成 ZIP，开始下载${r.error_count ? `（${r.error_count} 个失败见 manifest）` : ''}`);
    setTimeout(loadCodex, 1200);
  } catch(err) {
    showToast('下载 CPA 失败: ' + err.message);
  } finally {
    setTimeout(() => {
      btn.textContent = old;
      updateAccountSelectionUi();
    }, 1200);
  }
}

async function checkOnePlan(id, btn) {
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '查询中…';
  try {
    const r = await api('/api/accounts/check-plan', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_id: id}),
    });
    showToast(r.message || '套餐查询已加入后台队列');
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast(err.message);
    await pollAccountPlanStatuses();
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function extractOneLink(id, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  if (!acc) { showToast('账号不存在'); return; }
  const plan = (acc.current_plan_type || acc.plan_type || '').toString().toLowerCase();
  if (plan !== 'free' || !acc.plus_trial_eligible) {
    showToast('仅支持 free(可Plus试用) 账号提链');
    return;
  }
  if (!uiConfirmSync(`确定为该账号提链吗？\n\n${acc.email || ('#' + id)}\n\n提链类型按配置使用 PIX/UPI/KAKAO_PAY/IDEAL，成功会消耗 1 次 CDK。`)) return;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '提链中…';
  try {
    await api('/api/accounts/extract-link', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_id: id}),
    });
    showToast('提链任务已入队');
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('提链失败: ' + err.message);
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function extractSelectedLinks() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selected = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const eligible = selected.filter(a => (a.current_plan_type || a.plan_type || '').toString().toLowerCase() === 'free' && !!a.plus_trial_eligible);
  if (!eligible.length) { showToast('选中账号里没有 free(可Plus试用)'); return; }
  if (!uiConfirmSync(`确定批量提链 ${eligible.length} 个 free(可Plus试用) 账号吗？\n\n非可试用账号会自动跳过。成功会按账号消耗 CDK 次数。`)) return;
  const btn = $('#btnExtractSelectedLinksV2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '提链中…';
  try {
    const r = await api('/api/accounts/extract-link-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    const skipped = (r.skipped_count || 0) + (r.busy_count || 0) + (r.failed_count || 0);
    showToast(skipped ? `已入队 ${r.started_count || 0} 个，跳过/失败 ${skipped} 个` : `已入队 ${r.started_count || 0} 个`);
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('批量提链失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    btn.textContent = old;
  }
}

async function generateOneCodexAgent(id, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  if (!acc) { showToast('账号不存在'); return; }
  if (!acc.has_access_token) { showToast('该账号没有 access_token'); return; }
  if (!uiConfirmSync(`确定为该账号生成 Codex Agent Token 吗？\n\n${acc.email || ('#' + id)}\n\n会调用 auth.openai.com 注册 agent_identity，并保存到本地账号记录。`)) return;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '生成中…';
  try {
    await api('/api/accounts/codex-agent', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_id: id}),
    });
    showToast('Codex Agent Token 生成任务已入队');
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('生成失败: ' + err.message);
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function generateSelectedCodexAgents() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selected = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const eligible = selected.filter(a => a.has_access_token);
  if (!eligible.length) { showToast('选中账号里没有可用 access_token'); return; }
  if (!uiConfirmSync(`确定批量生成 ${eligible.length} 个 Codex Agent Token 吗？\n\n缺少 access_token 的账号会自动跳过。`)) return;
  const btn = $('#btnGenerateSelectedAgentV2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '生成中…';
  try {
    const r = await api('/api/accounts/codex-agent-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    const skipped = (r.skipped_count || 0) + (r.busy_count || 0) + (r.failed_count || 0);
    showToast(skipped ? `已入队 ${r.started_count || 0} 个，跳过/失败 ${skipped} 个` : `已入队 ${r.started_count || 0} 个`);
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('批量生成失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    btn.textContent = old;
  }
}

async function uploadOneCodexAgentSub2(id, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  if (!acc) { showToast('账号不存在'); return; }
  if ((acc.codex_agent_status || '') !== 'success') { showToast('该账号还没有生成 Agent Token'); return; }
  if (!uiConfirmSync(`确定上传该账号 Agent Token 到 sub2api 吗？\n\n${acc.email || ('#' + id)}`)) return;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '上传中…';
  try {
    await api(`/api/accounts/${encodeURIComponent(id)}/codex-agent/upload-sub2`, { method:'POST' });
    showToast('Agent Token 已上传 sub2api');
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('上传 sub2 失败: ' + err.message);
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function uploadSelectedCodexAgentSub2() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selected = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const ready = selected.filter(a => (a.codex_agent_status || '') === 'success');
  if (!ready.length) { showToast('选中账号里没有已生成的 Agent Token'); return; }
  if (!uiConfirmSync(`确定上传选中的 ${ready.length} 个 Agent Token 到 sub2api 吗？\n\n未生成成功的账号会自动跳过。`)) return;
  const btn = $('#btnUploadSelectedAgentSub2V2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '上传中…';
  try {
    const r = await api('/api/accounts/codex-agent/upload-sub2-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    const skipped = (r.skipped_count || 0) + (r.failed_count || 0);
    showToast(skipped ? `已上传 ${r.uploaded_count || 0} 个，跳过/失败 ${skipped} 个` : `已上传 ${r.uploaded_count || 0} 个`);
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('批量上传 sub2 失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    btn.textContent = old;
  }
}

function _ensureDownloadFrame(name) {
  let iframe = document.querySelector(`iframe[name="${name}"]`);
  if (!iframe) {
    iframe = document.createElement('iframe');
    iframe.name = name;
    iframe.style.display = 'none';
    document.body.appendChild(iframe);
  }
  return iframe;
}

function downloadOneCodexAgent(id) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  if (!acc) { showToast('账号不存在'); return; }
  if ((acc.codex_agent_status || '') !== 'success') { showToast('该账号还没有生成 Agent Token'); return; }
  _ensureDownloadFrame('codexAgentDownloadFrame');
  const a = document.createElement('a');
  a.href = `/api/accounts/${encodeURIComponent(id)}/codex-agent/download`;
  a.target = 'codexAgentDownloadFrame';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
  showToast('已提交 Agent Token 下载');
}

function downloadSelectedCodexAgents() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selected = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const ready = selected.filter(a => (a.codex_agent_status || '') === 'success');
  if (!ready.length) { showToast('选中账号里没有已生成的 Agent Token'); return; }
  if (!uiConfirmSync(`确定下载选中的 ${ready.length} 个 Codex Agent Token 吗？\n\n未生成成功的账号会自动跳过，文件会打包 ZIP。`)) return;
  const btn = $('#btnDownloadSelectedAgentV2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '下载中…';
  try {
    const frameName = 'codexAgentBulkDownloadFrame';
    _ensureDownloadFrame(frameName);
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/api/accounts/codex-agent/download-bulk';
    form.target = frameName;
    form.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'account_ids';
    input.value = JSON.stringify(ids);
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    setTimeout(() => form.remove(), 30000);
    showToast('已提交 Agent Token ZIP 下载');
  } catch(err) {
    showToast('下载 Agent Token 失败: ' + err.message);
  } finally {
    setTimeout(() => {
      btn.textContent = old;
      updateAccountSelectionUi();
    }, 1200);
  }
}

async function checkSelectedPlans() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const workers = getCodexBulkWorkers();
  const btn = $('#btnCheckSelectedPlansV2');
  if (!btn) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '查询中…';
  try {
    const r = await api('/api/accounts/check-plan-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, workers}),
    });
    const failed = r.failed_count || 0;
    const busy = r.busy_count || 0;
    showToast(`已入队 ${r.started_count || 0} 个，查询中跳过 ${busy} 个，入队失败 ${failed} 个`);
    await pollAccountPlanStatuses();
  } catch(err) {
    showToast('批量查询失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    btn.textContent = old;
  }
}

async function checkSelectedLive(idsArg = null, btnArg = null) {
  const ids = idsArg || Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const workers = getCodexBulkWorkers();
  const msg = idsArg ? `确定查活这个账号并刷新最新 AT/accessToken 吗？` : `确定查活选中的 ${ids.length} 个账号吗？

会重新登录邮箱 OTP；登录成功且未封号则标记正常，并刷新最新 AT/accessToken。

并发线程数：${workers}`;
  if (!uiConfirmSync(msg)) return;
  const firstAcc = ACCOUNTS.find(a => Number(a.id) === Number(ids[0]));
  const btn = btnArg || $('#btnCheckSelectedLiveV2');
  const old = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '查活中…';
  }
  try {
    const r = await api('/api/accounts/check-live-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, workers}),
    });
    const skipped = (r.skipped || []).length;
    showToast(`查活已入队 ${r.started_count || 0} 个，忙碌 ${r.busy_count || 0} 个，失败 ${r.failed_count || 0}${skipped ? `，跳过 ${skipped}` : ''}`);
    const firstStarted = (r.started || [])[0];
    if (firstStarted?.email) openLiveLog(firstStarted.email);
    else if (firstAcc && firstAcc.email) openLiveLog(firstAcc.email);
    await loadAccounts();
    loadSummary();
  } catch(err) {
    showToast('查活失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    if (btn) {
      btn.textContent = old;
      btn.disabled = false;
    }
  }
}

async function checkSelectedCodexOauth(idsArg = null, btnArg = null) {
  const ids = idsArg || Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const msg = idsArg
    ? '确定检测这个账号的 Codex OAuth 凭证吗？'
    : `确定检测选中的 ${ids.length} 个账号的 Codex OAuth 凭证吗？`;
  if (!uiConfirmSync(`${msg}\n\n检测只会读取 Codex usage，不触发邮箱 OTP、短信或模型请求。`)) return;
  const btn = btnArg || $('#btnCheckSelectedCodexOauthV2');
  const old = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '检测中…';
  }
  try {
    const r = await api('/api/accounts/check-codex-oauth-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    const skipped = (r.skipped || []).length;
    showToast(`Codex测活已入队 ${r.started_count || 0} 个，忙碌 ${r.busy_count || 0} 个，失败 ${r.failed_count || 0}${skipped ? `，跳过 ${skipped}` : ''}`);
    await loadAccounts();
    window.setTimeout(() => loadAccounts(), 800);
    window.setTimeout(() => loadAccounts(), 2200);
  } catch(err) {
    showToast('Codex OAuth 测活失败: ' + err.message);
    updateAccountSelectionUi();
  } finally {
    if (btn) {
      btn.textContent = old;
      btn.disabled = false;
    }
  }
}

async function deleteAccount(id, email, btn) {
  if (!uiConfirmSync(`确定删除账号 #${id}？\n\n${email || ''}\n\n会从本地账号列表、注册成功邮箱和 token 文件中移除。`)) return;
  btn.disabled = true;
  try {
    await api(`/api/accounts/${id}/delete`, { method:'POST', headers:{'Content-Type':'application/json'}, body: '{}' });
    ACCOUNT_SELECTED.delete(Number(id));
    showToast('账号已删除');
    loadAccounts(); loadSummary();
  } catch(err) {
    showToast('删除失败: ' + err.message);
    btn.disabled = false;
  }
}

async function archiveOneAccount(id, archived, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  const email = acc?.email || '';
  const action = archived ? '归档' : '恢复';
  if (!uiConfirmSync(`确定${action}账号 #${id}？\n\n${email}\n\n${archived ? '归档后默认账号列表不会查询/显示它，可勾选“查看归档”单独查看。' : '恢复后会回到默认账号列表。'}`)) return;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = `${action}中…`;
  try {
    await api(`/api/accounts/${id}/archive`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({archived}),
    });
    ACCOUNT_SELECTED.delete(Number(id));
    showToast(archived ? '账号已归档' : '账号已恢复');
    loadAccounts(); loadSummary();
  } catch(err) {
    showToast(`${action}失败: ` + err.message);
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function archiveSelectedAccounts() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const archived = !SHOW_ARCHIVED_ACCOUNTS;
  const action = archived ? '归档' : '恢复';
  if (!uiConfirmSync(`确定${action}选中的 ${ids.length} 个账号吗？\n\n${archived ? '归档后默认账号列表不会查询/显示这些账号，可勾选“查看归档”单独查看。' : '恢复后这些账号会回到默认账号列表。'}`)) return;
  const btn = $('#btnArchiveSelectedAccountsV2');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = `${action}中…`;
  try {
    const r = await api('/api/accounts/archive-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, archived}),
    });
    (r.updated || []).forEach(item => ACCOUNT_SELECTED.delete(Number(item.id)));
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已${action} ${r.updated_count || 0} 个，跳过 ${skippedCount} 个` : `已${action} ${r.updated_count || 0} 个`);
    loadAccounts(); loadSummary();
  } catch(err) {
    showToast(`批量${action}失败: ` + err.message);
    updateAccountSelectionUi();
  } finally {
    btn.textContent = old;
  }
}

async function deleteSelectedAccounts() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  if (!uiConfirmSync(`确定删除选中的 ${ids.length} 个账号吗？\n\n会从本地账号列表、注册成功邮箱和 token 文件中移除。`)) return;
  const _delBtn = $('#btnDeleteSelectedAccountsV2'); if (_delBtn) _delBtn.disabled = true;
  try {
    const r = await api('/api/accounts/delete-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids}),
    });
    (r.deleted || []).forEach(item => ACCOUNT_SELECTED.delete(Number(item.id)));
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已删除 ${r.deleted_count || 0} 个，跳过 ${skippedCount} 个` : `已删除 ${r.deleted_count || 0} 个`);
    loadAccounts(); loadSummary();
  } catch(err) {
    showToast('批量删除失败: ' + err.message);
    updateAccountSelectionUi();
  }
}

async function editAccountNote(id, btn) {
  const acc = ACCOUNTS.find(a => Number(a.id) === Number(id));
  if (!acc) { showToast('账号不存在'); return; }
  const note = prompt(`设置账号备注（留空可清空）\n\n${acc.email || ('#' + id)}`, acc.note || '');
  if (note === null) return;
  if (note.length > 2000) { showToast('备注最多 2000 个字符'); return; }
  btn.disabled = true;
  try {
    await api(`/api/accounts/${id}/note`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({note}),
    });
    showToast(note ? '备注已保存' : '备注已清空');
    loadAccounts();
  } catch(err) {
    showToast('保存备注失败: ' + err.message);
    btn.disabled = false;
  }
}

async function noteSelectedAccounts() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const note = prompt(`给选中的 ${ids.length} 个账号设置统一备注（留空可清空）`, '');
  if (note === null) return;
  if (note.length > 2000) { showToast('备注最多 2000 个字符'); return; }
  const btn = $('#btnNoteSelectedAccountsV2');
  if (!btn) return;
  btn.disabled = true;
  try {
    const r = await api('/api/accounts/note-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, note}),
    });
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已备注 ${r.updated_count || 0} 个，跳过 ${skippedCount} 个` : `已备注 ${r.updated_count || 0} 个`);
    loadAccounts();
  } catch(err) {
    showToast('批量备注失败: ' + err.message);
    updateAccountSelectionUi();
  }
}


async function stopSelectedCodex() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const selectedAccounts = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const retrying = selectedAccounts.filter(a => (a.codex_status || '') === 'retrying');
  if (retrying.length === 0) { showToast('选中账号里没有正在补跑的 Codex'); return; }
  if (!uiConfirmSync(`确定停止选中的 ${retrying.length} 个 Codex 补跑吗？\n\n会发送停止信号，并将状态标记为“已停止”。`)) return;
  const btn = $('#btnStopSelectedCodexV2');
  if (!btn) return;
  btn.disabled = true;
  try {
    const r = await api('/api/codex/stop-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: retrying.map(a => a.id)}),
    });
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已停止 ${r.stopped_count || 0} 个，跳过 ${skippedCount} 个` : `已停止 ${r.stopped_count || 0} 个`);
    loadAccounts();
    if (retryLogEmail) pollRetryLog();
  } catch(err) {
    showToast('停止选中补跑失败: ' + err.message);
    updateAccountSelectionUi();
  }
}

async function retrySelectedCodex() {
  const ids = Array.from(ACCOUNT_SELECTED);
  if (ids.length === 0) { showToast('请先选择账号'); return; }
  const workers = getCodexBulkWorkers();
  const workersElV2 = $('#codexBulkWorkersV2');
  if (workersElV2) workersElV2.value = workers;
  const selectedAccounts = ids.map(id => ACCOUNTS.find(a => Number(a.id) === Number(id))).filter(Boolean);
  const retryingCount = selectedAccounts.filter(a => (a.codex_status || '') === 'retrying').length;
  const deactivatedCount = selectedAccounts.filter(a => (a.live_check_status || '') === 'deactivated').length;
  let msg = `批量补跑选中的 ${ids.length} 个账号 Codex 授权？

并发线程数：${workers}

将按账号消耗邮箱 OTP 和接码短信。`;
  if (retryingCount || deactivatedCount) msg += `

其中：补跑中 ${retryingCount} 个、已废号 ${deactivatedCount} 个会自动跳过。`;
  if (!uiConfirmSync(msg)) return;
  const btn = $('#btnRetrySelectedCodexV2');
  if (!btn) return;
  btn.disabled = true;
  try {
    const r = await api('/api/codex/retry-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({account_ids: ids, workers}),
    });
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已开始 ${r.started_count || 0} 个，跳过 ${skippedCount} 个` : (r.message || '已开始批量补跑'));
    loadAccounts();
    const first = (r.started || [])[0];
    if (first && first.email) openRetryLog(first.email);
    const poll = setInterval(loadAccounts, 15000);
    setTimeout(() => clearInterval(poll), 10 * 60 * 1000);
  } catch(err) {
    showToast('批量补跑失败: ' + err.message);
    updateAccountSelectionUi();
  }
}

async function copyCurrentPageTokens() {
  const ids = ACCOUNTS.filter(r => r.has_access_token).map(r => Number(r.id));
  if (!ids.length) { showToast('当前页没有 Token'); return; }
  try { copyText((await fetchAccountSecrets(ids, 'access_token')).join('\n')); } catch(e) { showToast('复制失败: ' + e.message); }
}
async function copyCurrentPageLines() {
  const ids = ACCOUNTS.map(r => Number(r.id));
  if (!ids.length) { showToast('当前页没有账号'); return; }
  try { copyText((await fetchAccountSecrets(ids, 'copy_line')).join('\n')); } catch(e) { showToast('复制失败: ' + e.message); }
}
let EMAIL_CHANGE_ACCOUNT_IDS = [];
function openEmailChangeModal(ids) {
  EMAIL_CHANGE_ACCOUNT_IDS = (ids || []).map(Number).filter(Boolean);
  if (!EMAIL_CHANGE_ACCOUNT_IDS.length) { showToast('请先选择账号'); return; }
  $('#emailChangeHintV2').textContent = EMAIL_CHANGE_ACCOUNT_IDS.length === 1
    ? `将为账号 #${EMAIL_CHANGE_ACCOUNT_IDS[0]} 换绑新邮箱，请选择来源。`
    : `将为选中的 ${EMAIL_CHANGE_ACCOUNT_IDS.length} 个账号分别领取并换绑新邮箱。`;
  $('#emailChangeModalV2').classList.remove('hidden');
  updateModalScrollLock();
}
function closeEmailChangeModal() {
  $('#emailChangeModalV2').classList.add('hidden');
  EMAIL_CHANGE_ACCOUNT_IDS = [];
  updateModalScrollLock();
}
async function submitEmailChange() {
  const ids = EMAIL_CHANGE_ACCOUNT_IDS.slice();
  const source = $('#emailChangeSourceV2').value;
  if (!ids.length) return;
  const btn = $('#btnSubmitEmailChangeV2');
  btn.disabled = true;
  try {
    const url = ids.length === 1 ? `/api/accounts/${ids[0]}/change-email` : '/api/accounts/change-email-bulk';
    const body = ids.length === 1 ? {source} : {source, account_ids: ids};
    const r = await api(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    closeEmailChangeModal();
    ACCOUNT_SELECTED.clear();
    showToast(ids.length === 1 ? '邮箱换绑任务已开始' : `已开始 ${r.started_count || 0} 个换绑任务，跳过 ${(r.skipped || []).length} 个`);
    planStatusRevision = '';
    loadAccounts();
    const poll = setInterval(loadAccounts, 5000);
    setTimeout(() => clearInterval(poll), 10 * 60 * 1000);
  } catch (e) { showToast('邮箱换绑失败: ' + e.message); }
  finally { btn.disabled = false; }
}
$('#btnCloseEmailChangeV2').addEventListener('click', closeEmailChangeModal);
$('#btnCancelEmailChangeV2').addEventListener('click', closeEmailChangeModal);
$('#btnSubmitEmailChangeV2').addEventListener('click', submitEmailChange);
$('#emailChangeModalV2').addEventListener('click', e => { if (e.target.id === 'emailChangeModalV2') closeEmailChangeModal(); });
(function bindAccountsToolbarV2() {
  const bind = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('click', fn);
  };
  bind('btnCheckSelectedPlansV2', checkSelectedPlans);
  bind('btnExtractSelectedLinksV2', extractSelectedLinks);
  bind('btnRetrySelectedCodexV2', retrySelectedCodex);
  bind('btnDownloadSelectedCpaV2', downloadSelectedCpa);
  bind('btnStopSelectedCodexV2', stopSelectedCodex);
  bind('btnSetupSelectedTotpV2', setupSelectedTotp);
  bind('btnChangeSelectedEmailsV2', () => openEmailChangeModal(Array.from(ACCOUNT_SELECTED)));
  bind('btnNoteSelectedAccountsV2', noteSelectedAccounts);
  bind('btnCopySelectedLinesV2', copySelectedAccountLines);
  bind('btnCopySelectedTokensV2', copySelectedAccountTokens);
  bind('btnCopySelectedEmailsV2', copySelectedAccountEmails);
  bind('btnCopyAllTokensV2', copyCurrentPageTokens);
  bind('btnCopyAllLinesV2', copyCurrentPageLines);
  bind('btnDownloadSelectedTxtV2', downloadSelectedAccountTxt);
  bind('btnDownloadSelectedFullExportV2', downloadSelectedAccountFullExport);
  bind('btnCopySelectedFullExportV2', copySelectedAccountFullExport);
  bind('btnCopySelectedLoginCredentialsV2', copySelectedAccountLoginCredentials);
  bind('btnArchiveSelectedAccountsV2', archiveSelectedAccounts);
  bind('btnDeleteSelectedAccountsV2', deleteSelectedAccounts);
})();

// ---------- 邮箱池 ----------
function getPoolSource() {
  const v2 = document.getElementById('poolSourceV2');
  return (v2 && v2.value) || 'all';
}
const POOL_SOURCE_LABELS = {
  all: '全部邮箱池',
  outlook: 'Outlook 邮箱池',
  generic_api: '通用 API 邮箱池',
  imap: '通用 IMAP 邮箱池',
  cloudflare_domain: '域名邮箱池',
};
function setPoolSourceV2(val) {
  const hidden = document.getElementById('poolSourceV2');
  const btn = document.getElementById('poolSourceV2Btn');
  const wrap = document.getElementById('poolSourceV2Wrap');
  if (!hidden) return;
  const next = POOL_SOURCE_LABELS[val] ? val : 'all';
  hidden.value = next;
  if (btn) btn.textContent = POOL_SOURCE_LABELS[next] || next;
  if (wrap) {
    wrap.querySelectorAll('.outlook-v2-select-item').forEach(item => {
      item.classList.toggle('is-active', item.dataset.value === next);
    });
  }
}
function getOutlookQuery() {
  const el = document.getElementById('qOutlookV2');
  return (el ? el.value : '').trim();
}
function poolKey(r) { return `${r.source || getPoolSource() || 'all'}|${r.email || ''}`; }
function poolLabel(src) {
  return ({outlook:'Outlook', generic_api:'通用API', imap:'通用IMAP', cloudflare_domain:'域名邮箱'})[src] || src || '-';
}
async function loadOutlook() {
  try {
    const source = getPoolSource();
    const q = getOutlookQuery();
    const p = PAGERS.outlook;
    const res = await api(`/api/outlook?paged=1&page=${encodeURIComponent(p.page)}&page_size=${encodeURIComponent(p.size)}&source=${encodeURIComponent(source)}&q=${encodeURIComponent(q)}`);
    OUTLOOK = res.items || [];
    OUTLOOK_TOTAL = Number(res.total || OUTLOOK.length || 0);
    const totalPages = Math.max(1, Math.ceil(OUTLOOK_TOTAL / p.size));
    if (p.page > totalPages) { p.page = totalPages; return loadOutlook(); }
    renderOutlook();
  } catch(e) {}
}
function closeOutlookV2MoreMenus() {
  document.querySelectorAll('.outlook-table-v2 .acc-v2-more.open').forEach(el => el.classList.remove('open'));
}
function positionOutlookV2MoreMenu(wrap) {
  const btn = wrap.querySelector('.acc-v2-more-btn');
  const menu = wrap.querySelector('.acc-v2-more-menu');
  if (!btn || !menu) return;
  const rect = btn.getBoundingClientRect();
  const menuW = Math.max(menu.offsetWidth || 148, 148);
  const menuH = menu.offsetHeight || 0;
  let left = rect.right - menuW;
  left = Math.max(8, Math.min(left, window.innerWidth - menuW - 8));
  let top = rect.bottom + 4;
  if (top + menuH > window.innerHeight - 8 && rect.top - menuH - 4 > 8) {
    top = rect.top - menuH - 4;
  }
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}
function _outlookTokenCell(r) {
  if (r.access_token) {
    const id = copyId(r.access_token);
    return `<button type="button" class="acc-v2-token-copy" data-copy-id="${id}" title="复制 Token">复制</button>`;
  }
  return `<span class="acc-v2-token-none">无Token</span>`;
}
function _outlookMoreMenu(r) {
  const src = esc(r.source || getPoolSource() || 'all');
  const email = esc(r.email);
  const items = [
    cbtn('复制Token', r.access_token, 'good'),
    cbtn('复制整行', r.account_copy_line, 'good'),
    r.status !== 'available' ? `<button type="button" data-pool-act="available" data-email="${email}" data-source="${src}">恢复可用</button>` : '',
    r.status !== 'disabled' ? `<button type="button" data-pool-act="disabled" data-email="${email}" data-source="${src}">停用</button>` : '',
    r.status !== 'failed' ? `<button type="button" data-pool-act="failed" data-email="${email}" data-source="${src}">标失败</button>` : '',
  ].filter(Boolean).join('');
  return items;
}
function renderOutlook() {
  const total = OUTLOOK_TOTAL;
  const rows = OUTLOOK;
  const bodyV2 = $('#outlookBodyV2');
  if (!bodyV2) return;
  bodyV2.innerHTML = rows.map(r => {
      const src = esc(r.source || getPoolSource() || 'all');
      const email = esc(r.email);
      return `
    <tr>
      <td class="col-check"><input type="checkbox" class="outlook-row-check" data-email="${email}" data-source="${src}" ${OUTLOOK_SELECTED.has(poolKey(r)) ? 'checked' : ''}></td>
      <td class="col-email" title="${email}">
        <div class="acc-v2-email">${email || '-'}</div>
        <div class="acc-v2-sub" title="${esc(r.copy_line || '')}">${esc(short(r.copy_line, 70))}</div>
      </td>
      <td class="col-source">${esc(poolLabel(r.source))}</td>
      <td class="col-status">${pill(r.status)}</td>
      <td class="col-token">${_outlookTokenCell(r)}</td>
      <td class="col-time" title="${esc(formatDateTime(r.imported_at || r.created_at))}">${esc(formatDateTime(r.imported_at || r.created_at))}</td>
      <td class="col-time" title="${esc(formatDateTime(r.used_at))}">${esc(formatDateTime(r.used_at))}</td>
      <td class="col-actions">
        <div class="acc-v2-actions">
          ${cbtn('复制邮箱', r.copy_line, 'primary')}
          <button type="button" class="danger" data-pool-act="delete" data-email="${email}" data-source="${src}" title="删除邮箱素材">删除</button>
          <div class="acc-v2-more">
            <button type="button" class="acc-v2-more-btn" data-outlook-more-toggle aria-haspopup="true">更多</button>
            <div class="acc-v2-more-menu" role="menu">${_outlookMoreMenu(r)}</div>
          </div>
        </div>
      </td>
    </tr>`;
    }).join('') || '<tr><td colspan="8" style="text-align:center;color:#909399;padding:28px;">邮箱池为空</td></tr>';
  updateOutlookSelectionUi(rows);
  _renderPager('outlook', total);
}
function updateOutlookSelectionUi(pageRows = null) {
  const none = OUTLOOK_SELECTED.size === 0;
  [
    'btnDeleteSelectedOutlookV2',
    'btnMarkSelectedOutlookAvailableV2',
    'btnDisableSelectedOutlookV2',
    'btnFailSelectedOutlookV2',
  ].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = none;
  });

  if (!pageRows) pageRows = OUTLOOK;
  const pageKeys = pageRows.map(r => poolKey(r)).filter(Boolean);
  const checkedCount = pageKeys.filter(key => OUTLOOK_SELECTED.has(key)).length;
  const allChecked = pageKeys.length > 0 && checkedCount === pageKeys.length;
  const someChecked = checkedCount > 0 && checkedCount < pageKeys.length;
  const cbAll = document.getElementById('outlookSelectAllV2');
  if (!cbAll) return;
  cbAll.checked = allChecked;
  cbAll.indeterminate = someChecked;
  cbAll.disabled = pageKeys.length === 0;
}
function onPoolSourceChange(el) {
  if (el && el.id === 'poolSourceV2') setPoolSourceV2(el.value);
  PAGERS.outlook.page = 1;
  OUTLOOK_SELECTED.clear();
  const val = el ? el.value : getPoolSource();
  if (val === 'outlook' || val === 'generic_api' || val === 'imap') setImportSourceV2(val);
  loadOutlook();
}
function syncOutlookSelectAll(checked) {
  const pageRows = OUTLOOK;
  if (checked) pageRows.forEach(r => r.email && OUTLOOK_SELECTED.add(poolKey(r)));
  else pageRows.forEach(r => OUTLOOK_SELECTED.delete(poolKey(r)));
  renderOutlook();
}
function onOutlookBodyChange(e) {
  const cb = e.target.closest('.outlook-row-check');
  if (!cb) return;
  const email = cb.dataset.email;
  const source = cb.dataset.source || getPoolSource();
  const key = `${source}|${email}`;
  if (cb.checked) OUTLOOK_SELECTED.add(key);
  else OUTLOOK_SELECTED.delete(key);
  updateOutlookSelectionUi();
}
async function onOutlookBodyClick(e) {
  const moreToggle = e.target.closest('[data-outlook-more-toggle]');
  if (moreToggle) {
    e.preventDefault();
    e.stopPropagation();
    const wrap = moreToggle.closest('.acc-v2-more');
    if (!wrap) return;
    const willOpen = !wrap.classList.contains('open');
    closeOutlookV2MoreMenus();
    if (willOpen) {
      wrap.classList.add('open');
      positionOutlookV2MoreMenu(wrap);
    }
    return;
  }
  if (e.target.closest('.acc-v2-more-menu')) closeOutlookV2MoreMenus();

  const t = e.target.closest('[data-pool-act]');
  if (!t) return;
  const { poolAct, email } = t.dataset;
  const source = t.dataset.source || getPoolSource();
  try {
    if (poolAct === 'delete') {
      if (!uiConfirmSync(`确定从邮箱池删除 ${email}？此操作不可撤销。`)) return;
      const r = await api('/api/outlook/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, source}) });
      if (!r.deleted) {
        showToast('删除失败：邮箱不存在或来源不匹配');
        return;
      }
      OUTLOOK_SELECTED.delete(`${source}|${email}`);
      showToast('已删除');
    } else {
      const noteMap = {failed:'手动标记失败', available:'手动恢复可用', disabled:'手动停用'};
      await api('/api/outlook/status', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, status: poolAct, source, note: noteMap[poolAct] || '手动修改状态'}) });
      showToast(poolAct === 'failed' ? '已标失败' : poolAct === 'disabled' ? '已停用' : '已恢复可用');
    }
    loadOutlook(); loadSummary();
  } catch(err) { showToast('操作失败: ' + err.message); }
}
(function bindOutlookV2() {
  const qV2 = $('#qOutlookV2');
  if (qV2) qV2.addEventListener('input', debounce(() => {
    PAGERS.outlook.page = 1; loadOutlook();
  }, 250));
  const srcWrap = $('#poolSourceV2Wrap');
  const srcHidden = $('#poolSourceV2');
  if (srcWrap && srcHidden) {
    const btn = $('#poolSourceV2Btn');
    if (btn) btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !srcWrap.classList.contains('open');
      srcWrap.classList.toggle('open', willOpen);
      btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    srcWrap.querySelectorAll('.outlook-v2-select-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const val = item.dataset.value;
        setPoolSourceV2(val);
        srcWrap.classList.remove('open');
        if (btn) btn.setAttribute('aria-expanded', 'false');
        onPoolSourceChange(srcHidden);
      });
    });
    document.addEventListener('click', (e) => {
      if (srcWrap.contains(e.target)) return;
      srcWrap.classList.remove('open');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }
  const selectAllV2 = $('#outlookSelectAllV2');
  if (selectAllV2) selectAllV2.addEventListener('change', (e) => syncOutlookSelectAll(e.target.checked));
  const bodyV2 = $('#outlookBodyV2');
  if (bodyV2) {
    bodyV2.addEventListener('change', onOutlookBodyChange);
    bodyV2.addEventListener('click', onOutlookBodyClick);
  }
  const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };
  bind('btnMarkSelectedOutlookAvailableV2', () => bulkMarkOutlookStatus('available'));
  bind('btnDisableSelectedOutlookV2', () => bulkMarkOutlookStatus('disabled'));
  bind('btnFailSelectedOutlookV2', () => bulkMarkOutlookStatus('failed'));
  bind('btnDeleteSelectedOutlookV2', deleteSelectedOutlook);
  bind('btnImportOutlookV2', openOutlookImportModal);
  bind('copyAllEmailsV2', () => copyText(OUTLOOK.map(r=>r.copy_line).filter(Boolean).join('\n')));
  document.addEventListener('click', (e) => {
    if (e.target.closest('.outlook-table-v2 .acc-v2-more')) return;
    closeOutlookV2MoreMenus();
  });
  window.addEventListener('scroll', () => closeOutlookV2MoreMenus(), true);
  window.addEventListener('resize', () => closeOutlookV2MoreMenus());
  bindOutlookImportModal();
})();

const IMPORT_SOURCE_LABELS = {
  outlook: 'Outlook 邮箱池',
  generic_api: '通用 API 取码邮箱',
  imap: '通用 IMAP 取码邮箱',
};
function setImportSourceV2(val) {
  const hidden = document.getElementById('importSourceV2');
  const btn = document.getElementById('importSourceV2Btn');
  const wrap = document.getElementById('importSourceV2Wrap');
  if (!hidden) return;
  const next = IMPORT_SOURCE_LABELS[val] ? val : 'outlook';
  hidden.value = next;
  if (btn) btn.textContent = IMPORT_SOURCE_LABELS[next] || next;
  if (wrap) {
    wrap.querySelectorAll('.outlook-v2-select-item').forEach(item => {
      item.classList.toggle('is-active', item.dataset.value === next);
    });
  }
  const imapSettings = document.getElementById('imapImportSettingsV2');
  if (imapSettings) {
    imapSettings.classList.toggle('hidden', next !== 'imap');
    imapSettings.style.display = next === 'imap' ? 'grid' : 'none';
  }
  const text = document.getElementById('importTextV2');
  if (text) text.placeholder = next === 'imap'
    ? 'user@example.com----imapPassword\nuser2@example.com:imapPassword'
    : next === 'generic_api'
      ? 'email----取码地址'
      : 'email----password----clientId----refreshToken';
}
function openOutlookImportModal() {
  const modal = $('#outlookImportModal');
  if (!modal) return;
  const result = $('#importResultV2');
  if (result) result.innerHTML = '';
  const pool = getPoolSource();
  if (pool === 'outlook' || pool === 'generic_api' || pool === 'imap') setImportSourceV2(pool);
  else setImportSourceV2('outlook');
  modal.classList.remove('hidden');
  updateModalScrollLock();
  const ta = $('#importTextV2');
  if (ta) setTimeout(() => ta.focus(), 50);
}
function closeOutlookImportModal() {
  const modal = $('#outlookImportModal');
  if (!modal) return;
  const wrap = $('#importSourceV2Wrap');
  if (wrap) wrap.classList.remove('open');
  modal.classList.add('hidden');
  updateModalScrollLock();
}
function bindOutlookImportModal() {
  const modal = $('#outlookImportModal');
  if (!modal || modal.dataset.bound) return;
  modal.dataset.bound = '1';
  const closeBtn = $('#btnCloseOutlookImport');
  const cancelBtn = $('#btnCancelOutlookImport');
  const submitBtn = $('#btnSubmitOutlookImport');
  if (closeBtn) closeBtn.addEventListener('click', closeOutlookImportModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeOutlookImportModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeOutlookImportModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeOutlookImportModal();
  });
  const wrap = $('#importSourceV2Wrap');
  const btn = $('#importSourceV2Btn');
  if (wrap && btn) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !wrap.classList.contains('open');
      wrap.classList.toggle('open', willOpen);
      btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    wrap.querySelectorAll('.outlook-v2-select-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        setImportSourceV2(item.dataset.value);
        wrap.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      });
    });
    document.addEventListener('click', (e) => {
      if (wrap.contains(e.target)) return;
      wrap.classList.remove('open');
      btn.setAttribute('aria-expanded', 'false');
    });
  }
  if (submitBtn) submitBtn.addEventListener('click', () => doImportOutlook());
}
async function doImportOutlook() {
  const textEl = $('#importTextV2');
  const sourceEl = $('#importSourceV2');
  const registeredEl = $('#importAsRegisteredV2');
  const resultEl = $('#importResultV2');
  const submitBtn = $('#btnSubmitOutlookImport');
  const text = textEl ? textEl.value : '';
  if (!text.trim()) { showToast('请粘贴邮箱素材'); return; }
  if (submitBtn) submitBtn.disabled = true;
  try {
    const source = sourceEl ? sourceEl.value : 'outlook';
    const as_registered = registeredEl ? !!registeredEl.checked : false;
    const payload = {text, source, as_registered};
    if (source === 'imap') {
      payload.imap_server = ($('#imapServerV2')?.value || '').trim();
      payload.imap_port = Number($('#imapPortV2')?.value || 993);
      payload.imap_ssl = ($('#imapSslV2')?.value || 'true') === 'true';
      if (!payload.imap_server) { showToast('请填写 IMAP 服务器'); return; }
    }
    const r = await api('/api/outlook/import', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const mode = r.as_registered ? '已注册账号' : '邮箱池素材';
    const msg = `按 ${mode} 导入：解析 ${r.parsed} 行，新增 ${r.inserted}，跳过 ${r.skipped}${r.as_registered ? '；可到“账号”页选择后批量补跑 Codex' : ''}`;
    if (resultEl) resultEl.innerHTML = `<div class="banner info">${esc(msg)}</div>`;
    if (textEl) textEl.value = '';
    setPoolSourceV2(source);
    setImportSourceV2(source);
    loadOutlook(); loadAccounts(); loadSummary();
    showToast(`导入完成：新增 ${r.inserted}，跳过 ${r.skipped}`);
    setTimeout(closeOutlookImportModal, 600);
  } catch(e) {
    if (resultEl) resultEl.innerHTML = `<div class="banner warn">${esc(e.message)}</div>`;
    else showToast('导入失败: ' + e.message);
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}
async function bulkMarkOutlookStatus(status) {
  const items = Array.from(OUTLOOK_SELECTED).map(key => {
    const [source, ...rest] = key.split('|');
    return {source, email: rest.join('|')};
  });
  if (items.length === 0) { showToast('请先选择邮箱'); return; }
  const labelMap = {available:'未使用/可用', disabled:'停用', failed:'失败'};
  const label = labelMap[status] || status;
  if (!uiConfirmSync(`确定把选中的 ${items.length} 个邮箱标记为${label}吗？`)) return;
  const btnId = status === 'disabled'
    ? 'btnDisableSelectedOutlookV2'
    : status === 'failed'
      ? 'btnFailSelectedOutlookV2'
      : 'btnMarkSelectedOutlookAvailableV2';
  const btn = document.getElementById(btnId);
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/outlook/status-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({items, status, source: getPoolSource(), note: `批量标记${label}`}),
    });
    OUTLOOK_SELECTED.clear();
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已更新 ${r.updated_count || 0} 个，跳过 ${skippedCount} 个` : `已更新 ${r.updated_count || 0} 个`);
    loadOutlook(); loadSummary();
  } catch(err) {
    showToast('批量标记失败: ' + err.message);
    updateOutlookSelectionUi();
  }
}

async function deleteSelectedOutlook() {
  const items = Array.from(OUTLOOK_SELECTED).map(key => {
    const [source, ...rest] = key.split('|');
    return {source, email: rest.join('|')};
  });
  if (items.length === 0) { showToast('请先选择邮箱'); return; }
  if (!uiConfirmSync(`确定从邮箱池删除选中的 ${items.length} 个邮箱吗？\n\n此操作不可撤销。`)) return;
  const delBtn = document.getElementById('btnDeleteSelectedOutlookV2'); if (delBtn) delBtn.disabled = true;
  try {
    const r = await api('/api/outlook/delete-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({items, source: getPoolSource()}),
    });
    (r.deleted || []).forEach(item => OUTLOOK_SELECTED.delete(`${item.source}|${item.email}`));
    const skippedCount = (r.skipped || []).length;
    showToast(skippedCount ? `已删除 ${r.deleted_count || 0} 个，跳过 ${skippedCount} 个` : `已删除 ${r.deleted_count || 0} 个`);
    loadOutlook(); loadSummary();
  } catch(err) {
    showToast('批量删除失败: ' + err.message);
    updateOutlookSelectionUi();
  }
}
// ---------- Codex 授权 ----------
let CODEX = [];
let CODEX_TOTAL = 0;
function getCodexQuery() {
  const el = document.getElementById('qCodexV2');
  return (el ? el.value : '').trim();
}
let CODEX_SHOW_ARCHIVED = false;
async function loadCodex() {
  try {
    const p = PAGERS.codex;
    const q = getCodexQuery();
    const archived = CODEX_SHOW_ARCHIVED ? 'only' : '0';
    const dateFrom = document.getElementById('dateFromCodexV2')?.value || '';
    const dateTo = document.getElementById('dateToCodexV2')?.value || '';
    const r = await api(`/api/codex?paged=1&page=${encodeURIComponent(p.page)}&page_size=${encodeURIComponent(p.size)}&q=${encodeURIComponent(q)}&archived=${encodeURIComponent(archived)}&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`);
    CODEX = r.accounts || [];
    CODEX_TOTAL = Number(r.total || CODEX.length || 0);
    const totalPages = Math.max(1, Math.ceil(CODEX_TOTAL / p.size));
    if (p.page > totalPages) { p.page = totalPages; return loadCodex(); }
    const st = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    st('codexStatTotalV2', r.summary?.total ?? 0);
    st('codexStatExportedV2', r.summary?.exported ?? 0);
    st('codexStatPendingV2', r.summary?.pending ?? 0);
    renderCodex();
  } catch(e) { showToast('加载 Codex 列表失败: ' + e.message); }
}
const CODEX_SELECTED = new Set();
function _codexUpdateSelectedHint() {
  const none = CODEX_SELECTED.size === 0;
  const archiveBtn = document.getElementById('btnCodexArchiveBulkV2');
  if (archiveBtn) {
    archiveBtn.disabled = none;
    archiveBtn.textContent = CODEX_SHOW_ARCHIVED ? '恢复选中' : '归档选中';
    archiveBtn.title = CODEX_SHOW_ARCHIVED ? '把选中的归档凭证恢复到默认列表' : '归档选中的 Codex 授权凭证；默认列表不再显示';
  }
  ['btnCodexDownloadBulkV2', 'btnCodexDownloadBulkCpaV2', 'btnCodexDeleteBulkV2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = none;
  });
}
function _codexStatusBadge(r) {
  const exported = (r.exported_count || 0) > 0;
  return exported
    ? `<span class="pill status-used" title="导出 ${esc(r.exported_count)} 次，最近 ${esc(r.exported_at || '-')}">已导出</span>`
    : `<span class="pill status-available">未导出</span>`;
}
function closeCodexV2MoreMenus() {
  document.querySelectorAll('.codex-table-v2 .acc-v2-more.open').forEach(el => el.classList.remove('open'));
}
function positionCodexV2MoreMenu(wrap) {
  const btn = wrap.querySelector('.acc-v2-more-btn');
  const menu = wrap.querySelector('.acc-v2-more-menu');
  if (!btn || !menu) return;
  const rect = btn.getBoundingClientRect();
  const menuW = Math.max(menu.offsetWidth || 148, 148);
  const menuH = menu.offsetHeight || 0;
  let left = rect.right - menuW;
  left = Math.max(8, Math.min(left, window.innerWidth - menuW - 8));
  let top = rect.bottom + 4;
  if (top + menuH > window.innerHeight - 8 && rect.top - menuH - 4 > 8) {
    top = rect.top - menuH - 4;
  }
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}
function syncCodexSelectAllUi(pageRows = null) {
  if (!pageRows) pageRows = CODEX;
  const pageFilenames = pageRows.map(r => r.filename);
  const allChecked = pageFilenames.length > 0 && pageFilenames.every(f => CODEX_SELECTED.has(f));
  const someChecked = pageFilenames.some(f => CODEX_SELECTED.has(f));
  const cb = document.getElementById('codexSelectAllV2');
  if (!cb) return;
  cb.checked = allChecked;
  cb.indeterminate = !allChecked && someChecked;
  cb.disabled = pageFilenames.length === 0;
}
function renderCodex() {
  const total = CODEX_TOTAL;
  const rows = CODEX;
  const body = $('#codexBodyV2');
  if (!body) return;
  body.innerHTML = rows.map(r => {
    const checked = CODEX_SELECTED.has(r.filename) ? 'checked' : '';
    const exported = (r.exported_count || 0) > 0;
    const moreItems = [
      `<button type="button" class="good" data-codex-download-cpa="${esc(r.filename)}" title="按邮箱从 CPA auth-files 下载真实 Codex JSON">从CPA下载</button>`,
      exported ? `<button type="button" data-codex-reset="${esc(r.filename)}" title="重新标记为未导出">重置标记</button>` : '',
      `<button type="button" class="${r.archived ? 'good' : ''}" data-codex-archive="${esc(r.filename)}" data-archived="${r.archived ? '0' : '1'}" title="${r.archived ? '恢复到默认 Codex 列表' : '归档后默认列表不再显示'}">${r.archived ? '恢复' : '归档'}</button>`,
    ].filter(Boolean).join('');
    return `
    <tr>
      <td class="col-check"><input type="checkbox" class="codex-row-check" data-fname="${esc(r.filename)}" ${checked}></td>
      <td class="col-email" title="${esc(r.email || '')}">
        <div class="acc-v2-email">${esc(r.email || '-')}${r.archived ? ' <span class="pill status-used" title="已归档">归档</span>' : ''}</div>
        <div class="acc-v2-sub" title="${esc(r.filename)}">${esc(r.filename)}</div>
      </td>
      <td class="col-phone-info">${_codexCredentialPhoneInfoV2(r)}</td>
      <td class="col-plan">${esc(r.plan || '-')}</td>
      <td class="col-status">${_codexStatusBadge(r)}</td>
      <td class="col-account" title="${esc(r.account_id || '-')}">${esc(r.account_id || '-')}</td>
      <td class="col-time" title="${esc(formatDateTime(r.mtime))}">${esc(formatDateTime(r.mtime))}</td>
      <td class="col-time" title="${esc(formatDateTime(r.expired))}">${esc(formatDateTime(r.expired))}</td>
      <td class="col-actions">
        <div class="acc-v2-actions">
          <button type="button" class="primary" data-codex-download="${esc(r.filename)}" title="从 SQLite 下载 JSON/回执">下载凭证</button>
          <button type="button" class="danger" data-codex-delete="${esc(r.filename)}" title="删除本地 Codex JSON 凭证文件">删除</button>
          <div class="acc-v2-more">
            <button type="button" class="acc-v2-more-btn" data-codex-more-toggle aria-haspopup="true">更多</button>
            <div class="acc-v2-more-menu" role="menu">${moreItems}</div>
          </div>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="9" style="text-align:center;color:#909399;padding:28px;">还没有 Codex 凭证</td></tr>';
  syncCodexSelectAllUi(rows);
  _codexUpdateSelectedHint();
  _renderPager('codex', total);
}
function syncCodexSelectAll(checked) {
  const pageRows = CODEX;
  if (checked) pageRows.forEach(r => CODEX_SELECTED.add(r.filename));
  else pageRows.forEach(r => CODEX_SELECTED.delete(r.filename));
  renderCodex();
}
function onCodexBodyChange(e) {
  const cb = e.target.closest('.codex-row-check');
  if (!cb) return;
  if (cb.checked) CODEX_SELECTED.add(cb.dataset.fname);
  else CODEX_SELECTED.delete(cb.dataset.fname);
  _codexUpdateSelectedHint();
  syncCodexSelectAllUi();
}
async function onCodexBodyClick(e) {
  const moreToggle = e.target.closest('[data-codex-more-toggle]');
  if (moreToggle) {
    e.preventDefault();
    e.stopPropagation();
    const wrap = moreToggle.closest('.acc-v2-more');
    if (!wrap) return;
    const willOpen = !wrap.classList.contains('open');
    closeCodexV2MoreMenus();
    if (willOpen) {
      wrap.classList.add('open');
      positionCodexV2MoreMenu(wrap);
    }
    return;
  }
  if (e.target.closest('.acc-v2-more-menu')) closeCodexV2MoreMenus();

  const dlCpa = e.target.closest('[data-codex-download-cpa]');
  if (dlCpa) {
    const fname = dlCpa.dataset.codexDownloadCpa;
    dlCpa.disabled = true;
    try {
      const resp = await fetch(`/api/codex/download-from-cpa/${encodeURIComponent(fname)}`);
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.error || ('HTTP ' + resp.status));
      }
      const cd = resp.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="([^"]+)"/);
      const dlname = m ? m[1] : fname.replace(/-cpa-callback\.json$/i, '-free.json');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = dlname;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
      showToast('已从 CPA 下载');
      setTimeout(loadCodex, 600);
    } catch(err) {
      showToast('从 CPA 下载失败: ' + err.message);
      dlCpa.disabled = false;
    }
    return;
  }
  const dl = e.target.closest('[data-codex-download]');
  if (dl) {
    const fname = dl.dataset.codexDownload;
    window.location.href = `/api/codex/download/${encodeURIComponent(fname)}`;
    setTimeout(loadCodex, 800);
    return;
  }
  const del = e.target.closest('[data-codex-delete]');
  if (del) {
    const fname = del.dataset.codexDelete;
    if (!uiConfirmSync(`确定删除 Codex 凭证？\n\n${fname}\n\n会删除本地 JSON 文件，并清理导出标记。此操作不可撤销。`)) return;
    del.disabled = true;
    try {
      await api('/api/codex/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filename: fname}) });
      CODEX_SELECTED.delete(fname);
      showToast('Codex 凭证已删除');
      loadCodex();
    } catch(err) {
      showToast('删除失败: ' + err.message);
      del.disabled = false;
    }
    return;
  }
  const rs = e.target.closest('[data-codex-reset]');
  if (rs) {
    const fname = rs.dataset.codexReset;
    if (!uiConfirmSync(`把 ${fname} 标记重置为未导出？`)) return;
    try {
      await api('/api/codex/reset-export', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filename: fname}) });
      showToast('已重置');
      loadCodex();
    } catch(err) { showToast('重置失败: ' + err.message); }
  }
  const ar = e.target.closest('[data-codex-archive]');
  if (ar) {
    const fname = ar.dataset.codexArchive;
    const archived = ar.dataset.archived === '1';
    ar.disabled = true;
    try {
      await api('/api/codex/archive', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filename: fname, archived}) });
      CODEX_SELECTED.delete(fname);
      showToast(archived ? '已归档' : '已恢复');
      loadCodex();
    } catch(err) {
      showToast('归档失败: ' + err.message);
      ar.disabled = false;
    }
  }
}
async function codexArchiveSelected() {
  if (CODEX_SELECTED.size === 0) return;
  const filenames = Array.from(CODEX_SELECTED);
  const archived = !CODEX_SHOW_ARCHIVED;
  if (!uiConfirmSync(`${archived ? '归档' : '恢复'}选中的 ${filenames.length} 个 Codex 授权凭证？`)) return;
  try {
    const r = await api('/api/codex/archive-bulk', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({filenames, archived}),
    });
    (r.updated || []).forEach(item => CODEX_SELECTED.delete(item.filename));
    showToast(`${archived ? '归档' : '恢复'}成功 ${r.updated_count || 0} 个`);
    loadCodex();
  } catch(err) { showToast('归档失败: ' + err.message); }
}
function applyCodexArchivedFilter(on) {
  CODEX_SHOW_ARCHIVED = !!on;
  const btn = document.getElementById('showArchivedCodexV2');
  if (btn) {
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('is-active', on);
  }
  CODEX_SELECTED.clear();
  PAGERS.codex.page = 1;
  loadCodex();
}
async function codexDownloadBulkLocal() {
  if (CODEX_SELECTED.size === 0) return;
  const filenames = Array.from(CODEX_SELECTED);
  const note = `选中 ${filenames.length} 个凭证，打包到一个 JSON 文件下载。\n\n` +
               `⚠ 这个聚合格式 CPA 不能直接读，主要用于备份/迁移。\n` +
               `要给 CPA 用请用每行的"下载 JSON"单个下载。\n\n继续？`;
  if (!uiConfirmSync(note)) return;
  const btn = $('#btnCodexDownloadBulkV2');
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch('/api/codex/download-bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames }),
    });
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.error || ('HTTP ' + resp.status));
    }
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="([^"]+)"/);
    const dlname = m ? m[1] : `codex-bulk-${Date.now()}.json`;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = dlname;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
    showToast(`已打包下载 ${filenames.length} 个`);
    CODEX_SELECTED.clear();
    setTimeout(loadCodex, 600);
  } catch(err) {
    showToast('批量下载失败: ' + err.message);
  } finally {
    _codexUpdateSelectedHint();
  }
}
async function codexDownloadBulkCpa() {
  if (CODEX_SELECTED.size === 0) return;
  const filenames = Array.from(CODEX_SELECTED);
  const note = `选中 ${filenames.length} 个本地记录，将按邮箱到 CPA auth-files 查找并下载真实 Codex JSON。\n\n` +
               `下载结果会打包成 ZIP，解压后每个 JSON 可直接放入 CPA auth-dir。\n` +
               `如 CPA 中找不到对应邮箱，会在 zip 的 manifest.json 记录错误。\n\n继续？`;
  if (!uiConfirmSync(note)) return;
  const btn = $('#btnCodexDownloadBulkCpaV2');
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch('/api/codex/download-bulk-from-cpa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames }),
    });
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      const detail = Array.isArray(e.errors) && e.errors.length ? ('；' + e.errors.slice(0, 3).map(x => `${x.filename}: ${x.error}`).join('；')) : '';
      throw new Error((e.error || ('HTTP ' + resp.status)) + detail);
    }
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="([^"]+)"/);
    const dlname = m ? m[1] : `codex-cpa-bulk-${Date.now()}.zip`;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = dlname;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 800);
    showToast(`已从 CPA 打包下载 ${filenames.length} 个（详情见 manifest.json）`);
    CODEX_SELECTED.clear();
    setTimeout(loadCodex, 600);
  } catch(err) {
    showToast('从 CPA 批量下载失败: ' + err.message);
  } finally {
    _codexUpdateSelectedHint();
  }
}
async function codexDeleteBulk() {
  if (CODEX_SELECTED.size === 0) return;
  const filenames = Array.from(CODEX_SELECTED);
  if (!uiConfirmSync(`确定删除选中的 ${filenames.length} 个 Codex 凭证吗？\n\n会删除 SQLite 中的凭证记录。此操作不可撤销。`)) return;
  const btn = $('#btnCodexDeleteBulkV2');
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/codex/delete-bulk', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({filenames}),
    });
    (r.deleted || []).forEach(fname => CODEX_SELECTED.delete(fname));
    const skipped = (r.skipped || []).length;
    showToast(skipped ? `已删除 ${r.deleted_count || 0} 个，跳过 ${skipped} 个` : `已删除 ${r.deleted_count || 0} 个`);
    loadCodex();
  } catch(err) {
    showToast('批量删除失败: ' + err.message);
    _codexUpdateSelectedHint();
  }
}
(function bindCodexV2() {
  const qV2 = $('#qCodexV2');
  if (qV2) qV2.addEventListener('input', debounce(() => {
    PAGERS.codex.page = 1; loadCodex();
  }, 250));
  const selectAllV2 = $('#codexSelectAllV2');
  if (selectAllV2) selectAllV2.addEventListener('change', (e) => syncCodexSelectAll(e.target.checked));
  const bodyV2 = $('#codexBodyV2');
  if (bodyV2) {
    bodyV2.addEventListener('change', onCodexBodyChange);
    bodyV2.addEventListener('click', onCodexBodyClick);
  }
  const refreshV2 = $('#btnRefreshCodexV2');
  if (refreshV2) refreshV2.addEventListener('click', loadCodex);
  const archivedV2 = $('#showArchivedCodexV2');
  if (archivedV2) archivedV2.addEventListener('click', () => applyCodexArchivedFilter(!CODEX_SHOW_ARCHIVED));
  bindDateFilterPanel({
    btnId: 'btnDateFilterCodexV2',
    panelId: 'dateFilterPanelCodexV2',
    fromId: 'dateFromCodexV2',
    toId: 'dateToCodexV2',
    onApply: () => { PAGERS.codex.page = 1; loadCodex(); },
  });
  const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener('click', fn); };
  bind('btnCodexDownloadBulkV2', codexDownloadBulkLocal);
  bind('btnCodexDownloadBulkCpaV2', codexDownloadBulkCpa);
  bind('btnCodexArchiveBulkV2', codexArchiveSelected);
  bind('btnCodexDeleteBulkV2', codexDeleteBulk);
  document.addEventListener('click', (e) => {
    if (e.target.closest('.codex-table-v2 .acc-v2-more')) return;
    closeCodexV2MoreMenus();
  });
  window.addEventListener('scroll', () => closeCodexV2MoreMenus(), true);
  window.addEventListener('resize', () => closeCodexV2MoreMenus());
})();

// ---------- 配置 ----------
let CONFIG_EMAIL_ACTIVE_SECTION_V2 = '通用邮箱 / OTP';
let CONFIG_SMS_ACTIVE_SECTION_V2 = '通用接码';
let CONFIG_CODEX_ACTIVE_SECTION_V2 = '基础配置';
const CONFIG_PENDING_UPDATES = {};

function configGroups() {
  const groups = {};
  CONFIG.forEach(f => { (groups[f.group || '其他'] = groups[f.group || '其他'] || []).push(f); });
  return groups;
}

function emailConfigSectionForKey(key) {
  if (['USE_EMAIL_SERVICE','REGISTER_EMAIL','REGISTER_NAME','OTP_MAX_WAIT','OTP_POLL_INTERVAL','EMAIL_SOURCE'].includes(key)) {
    return ['通用邮箱 / OTP', '邮箱来源选择、OTP 等待参数、手动邮箱等通用设置'];
  }
  if (key.startsWith('GPTMAIL_')) return ['GPTMail', 'GPTMail 临时邮箱 API 配置'];
  if (key.startsWith('MAIL_NEST_')) return ['MailNest', 'MailNest / 迈巢临时邮箱 API 配置'];
  if (key.startsWith('CLOUDMAIL_')) return ['CloudMail', 'CloudMail 域名随机邮箱、Token 和收信 API 配置'];
  if (key.startsWith('REMAIL_')) return ['Remail', 'Remail 开放 API、项目下单和取件配置'];
  if (key.startsWith('OUTLOOK_')) return ['Outlook 邮箱池', 'Outlook 邮箱池和取件模式配置'];
  if (key.startsWith('IMAP_')) return ['通用 IMAP', '通用 IMAP 邮箱池的收件目录配置'];
  if (key.startsWith('CLOUDFLARE_')) return ['Cloudflare 临时邮箱', 'Cloudflare Worker 临时邮箱 API、鉴权与路径配置'];
  if (['EMAIL_DOMAIN','QQ_EMAIL','QQ_IMAP_PASSWORD'].includes(key)) return ['Cloudflare 域名邮箱', 'Cloudflare 转发到 QQ 邮箱后的 IMAP 收信配置'];
  return ['其他邮箱配置', ''];
}

function smsConfigSectionForKey(key) {
  if (['SMS_PROVIDER','SMS_PROVIDER_CHAIN','SMS_COUNTRY','SMS_SERVICE','SMS_MAX_PRICE','SMS_REJECT_VOIP_NUMBERS','SMS_MAX_RETRIES','SMS_CODE_WAIT','SMS_POLL_INTERVAL','SMS_REQUEST_TIMEOUT','SMS_RELEASE_RETRIES','SMS_RELEASE_RETRY_DELAY','SMS_API_KEY'].includes(key)) {
    return ['通用接码', '所有短信平台共用的通道顺序、国家/服务代码、人民币价格上限、等待、轮询、释放和重试参数'];
  }
  if (key.startsWith('SMS_POOL_')) return ['低价号码池', '号码池采购、账号测活与并发补跑参数'];
  if (key.startsWith('SMSBOWER_')) return ['SMSBower', 'SMSBower API、V2取号、随机选国、供应商、价格和地区排除配置'];
  if (key.startsWith('TIGER_SMS_')) return ['Tiger SMS', 'Tiger SMS API、V2取号、随机选国、供应商和地区排除配置'];
  if (key.startsWith('HEROSMS_')) return ['HeroSMS', 'HeroSMS API、V2取号、服务/国家、价格、供应商和地区排除配置'];
  if (key.startsWith('SMSPOOL_')) return ['SMSPool', 'SMSPool API、服务/国家覆盖、价格、号池和地区排除配置'];
  if (key.startsWith('VAKSMS_')) return ['Vak SMS', 'Vak SMS API、V0/V2取号、服务/国家、价格、运营商和地区排除配置'];
  return ['通用接码', '其他接码平台兼容字段；未归属平台的参数集中放在这里'];
}

function codexConfigSectionForKey(key) {
  if (['CODEX_OAUTH_DRIVER','CODEX_AUTH_URL_SOURCE'].includes(key)) {
    return ['基础配置', 'Codex 授权驱动和授权地址来源配置'];
  }
  if (key.startsWith('CPA_')) {
    return ['CPA配置', 'CPA 授权链接生成、回调上传和管理接口配置'];
  }
  if (['SUB2API_API_BASE','SUB2API_API_KEY','SUB2API_API_TIMEOUT'].includes(key)) {
    return ['sub2api通用', 'sub2api 共用 API 基址、鉴权和超时配置；Agent Token 和 OAuth 都复用这里'];
  }
  if (['SUB2API_AUTO_EXPORT','SUB2API_SYNC_MODE','SUB2API_OUTPUT_PATH','SUB2API_PROXY_KEY'].includes(key)) {
    return ['sub2api Agent Token', 'Codex Agent Token 生成后的 sub2api 同步、上传路径、本地文件和代理键配置'];
  }
  return ['基础配置', ''];
}

function renderRoxyWorkspaceToolsV2() {
  const current = (CONFIG.find(f => f.key === 'ROXY_WORKSPACE_ID') || {}).value || '';
  return `
    <div class="roxy-workspace-box" style="margin-top:18px;margin-bottom:4px;">
      <div>
        <b>团队 / 项目选择</b>
        <div class="hint">点击“获取团队”调用 Roxy <span class="mono">/browser/workspace</span>，选择后保存团队与项目 ID。</div>
        <a class="roxy-invite-link" href="https://roxybrowser.cn/invite/NvH4Jx" target="_blank" rel="noopener noreferrer">打开 RoxyBrowser 官网（免费 5 个窗口）</a>
      </div>
      <div class="row">
        <div>
          <label class="fld">团队 / 项目
            <select id="roxyWorkspaceSelectV2"><option value="">当前团队：${esc(current || '未设置')}</option></select>
          </label>
        </div>
        <div class="action-cell">
          <button class="btn" type="button" id="btnLoadRoxyWorkspacesV2">获取团队</button>
          <button class="btn primary" type="button" id="btnSaveRoxyWorkspaceV2" disabled>保存选择</button>
        </div>
      </div>
      <div id="roxyWorkspaceStatusV2" class="muted" style="font-size:12px;">未加载</div>
    </div>
  `;
}

function bindRoxyWorkspaceToolsV2() {
  const loadBtn = $('#btnLoadRoxyWorkspacesV2');
  const saveBtn = $('#btnSaveRoxyWorkspaceV2');
  const sel = $('#roxyWorkspaceSelectV2');
  if (!loadBtn || !saveBtn || !sel) return;
  if (loadBtn.dataset.bound) return;
  loadBtn.dataset.bound = '1';
  sel.addEventListener('change', () => { saveBtn.disabled = !sel.value; });
  loadBtn.addEventListener('click', () => loadRoxyWorkspaces());
  saveBtn.addEventListener('click', () => saveRoxyWorkspaceSelection());
}

function configGroupSlug(name) {
  return 'cfg-' + String(name || '')
    .replace(/[^\w\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
}
function configGroupDisplayName(name) {
  if (name === 'CloakBrowser') return '本地指纹浏览器';
  return name;
}
function configNavIcon(name) {
  const n = String(name || '');
  if (n.includes('网站') || n.includes('WebUI')) return '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>';
  if (n.includes('开关') || n.includes('功能')) return '<rect x="1" y="8" width="22" height="8" rx="4"/><circle cx="7" cy="12" r="2.8"/>';
  if (n === '本地指纹浏览器' || n.includes('Cloak')) return '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>';
  if (n.includes('邮箱') || n.includes('OTP')) return '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><path d="M22 6l-10 7L2 6"/>';
  if (n.includes('接码') || n.includes('SMS')) return '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>';
  if (n.includes('Codex')) return '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>';
  if (n.includes('代理') || n.includes('Proxy')) return '<circle cx="12" cy="12" r="3"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.93 4.93l1.41 1.41"/><path d="M17.66 17.66l1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/>';
  if (n.includes('画像') || n.includes('指纹')) return '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>';
  if (n.includes('人工') || n.includes('节奏')) return '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>';
  if (n.includes('提链')) return '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>';
  if (n.includes('Browser') || n.includes('Roxy') || n.includes('Skyvern') || n.includes('注册')) return '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>';
  return '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>';
}
function getConfigGroupNames() {
  const groups = {};
  (CONFIG || []).forEach(f => { groups[f.group || '其他'] = true; });
  return Object.keys(groups);
}
function configSectionIntro(name) {
  if (name === '网站配置') return '配置网站登录授权码与 Session 签名密钥。';
  if (name === '功能开关') return '';
  if (name === '注册方式') return '选择账号注册时使用的自动化方式。';
  if (name === 'CloakBrowser') return '本地指纹浏览器运行参数、语言时区与代理设置。';
  if (name === 'Browser Use') return 'Browser Use Cloud 远端浏览器与代理参数。';
  if (name === 'Skyvern') return 'Skyvern Browser Sessions 与代理参数。';
  if (name === 'RoxyBrowser') return 'RoxyBrowser API、环境与代理相关设置。';
  if (name === 'Codex') return 'Codex 授权驱动、CPA 与 sub2api 相关设置。';
  if (name === '邮箱 / OTP') return '邮箱来源、OTP 等待与各邮箱服务商参数。';
  if (name === '接码平台') return '接码通道、国家代码与本地取号服务参数。';
  if (name === '人工节奏') return '注册流程中的随机停顿节奏。';
  if (name === '浏览器画像') return '浏览器语言、时区、出口 IP 画像与注册省流量设置。';
  if (name === '代理池') return '代理列表与套餐/Agent 网络模式。';
  if (name === '提链') return '提链服务地址、CDK 与并发参数。';
  return '';
}
function configFeatureIcon(key) {
  if (key === 'ENABLE_CODEX_AUTO') {
    return {
      cls: 'config-switch-v2-icon--codex',
      svg: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
    };
  }
  if (key === 'ENABLE_2FA') {
    return {
      cls: 'config-switch-v2-icon--2fa',
      svg: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>'
    };
  }
  if (key === 'ENABLE_FLOW_TRIGGER') {
    return {
      cls: 'config-switch-v2-icon--flow',
      svg: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'
    };
  }
  return {
    cls: 'config-switch-v2-icon--default',
    svg: '<path d="M12 2v4"/><path d="M12 18v4"/><path d="M4.93 4.93l2.83 2.83"/><path d="M16.24 16.24l2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><path d="M4.93 19.07l2.83-2.83"/><path d="M16.24 7.76l2.83-2.83"/>'
  };
}
function renderConfigSectionHead(name, meta = '') {
  const title = configGroupDisplayName(name);
  return `
    <div class="config-section-v2-head">
      <div class="config-section-v2-head-main">
        <h3>${esc(title)}</h3>
        ${meta ? `<p class="config-section-v2-meta">${esc(meta)}</p>` : ''}
      </div>
      <span class="config-section-v2-head-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">${configNavIcon(name)}</svg>
      </span>
    </div>
  `;
}
function registrationDriverChoices() {
  return [
    { value: 'protocol', label: '纯协议注册' },
    { value: 'roxy', label: 'RoxyBrowser' },
    { value: 'cloak', label: '本地指纹浏览器' },
    { value: 'browser_use', label: 'browser_use' },
    { value: 'skyvern', label: 'skyvern' },
  ];
}
function getRegistrationDriverField() {
  return (CONFIG || []).find(f => f.key === 'REGISTRATION_DRIVER') || null;
}
function getRegistrationDriverValue() {
  const f = getRegistrationDriverField();
  if (!f) return 'protocol';
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  return String(fv == null ? 'protocol' : fv).trim().toLowerCase() || 'protocol';
}
function getAutoPlanCheckField() {
  return (CONFIG || []).find(f => f.key === 'AUTO_PLAN_CHECK_AFTER_REGISTER') || null;
}
function getAutoPlanCheckValue() {
  const f = getAutoPlanCheckField();
  if (!f) return false;
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  return !!fv;
}
function getProtocolFingerprintReuseField() {
  return (CONFIG || []).find(f => f.key === 'PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL') || null;
}
function getProtocolFingerprintReuseValue() {
  const f = getProtocolFingerprintReuseField();
  if (!f) return false;
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  return !!fv;
}
function registrationDriverLabel(value) {
  const cur = String(value || '').trim().toLowerCase();
  const hit = registrationDriverChoices().find(c => String(c.value) === cur);
  return hit ? String(hit.label || hit.value) : (cur || '—');
}
function closeConfigEpSelects(exceptId) {
  document.querySelectorAll('.config-ep-select.open').forEach(el => {
    if (exceptId && el.id === exceptId) return;
    el.classList.remove('open');
  });
}
function toggleConfigEpSelect(wrapId, e) {
  if (e) { e.preventDefault(); e.stopPropagation(); }
  const wrap = document.getElementById(wrapId);
  if (!wrap) return;
  const willOpen = !wrap.classList.contains('open');
  closeConfigEpSelects(wrapId);
  wrap.classList.toggle('open', willOpen);
}
function setRegistrationDriverValue(value) {
  const cur = String(value || 'protocol').trim().toLowerCase() || 'protocol';
  CONFIG_PENDING_UPDATES.REGISTRATION_DRIVER = cur;
  closeConfigEpSelects();
  renderRegistrationDriverCard();
  const sectionMount = document.getElementById('configRegistrationSelectV2');
  if (sectionMount) renderRegistrationEpSelect(sectionMount, 'configRegistrationSelectV2');
  bindRoxyWorkspaceToolsV2();
  bindCloudMailToolsV2();
}
function renderRegistrationEpSelect(mount, wrapId) {
  if (!mount) return;
  const cur = getRegistrationDriverValue();
  const label = registrationDriverLabel(cur);
  const items = registrationDriverChoices().map(c => `
    <button type="button" class="config-ep-select-item${c.value === cur ? ' is-active' : ''}" role="option" data-driver-value="${attrEsc(c.value)}">${esc(c.label)}</button>
  `).join('');
  mount.className = 'config-ep-select';
  mount.id = wrapId;
  mount.innerHTML = `
    <button type="button" class="config-ep-select-btn" data-ep-toggle="${attrEsc(wrapId)}">${esc(label)}</button>
    <div class="config-ep-select-menu" role="listbox">${items}</div>
    <input type="hidden" data-key="REGISTRATION_DRIVER" value="${attrEsc(cur)}">
  `;
}
function renderRegistrationDriverCard() {
  const cur = getRegistrationDriverValue();
  const label = registrationDriverLabel(cur);
  const labelEl = document.getElementById('configCardRegistrationLabelV2');
  const hintEl = document.getElementById('configCardRegistrationHintV2');
  if (labelEl) {
    labelEl.textContent = label;
    labelEl.classList.toggle('is-placeholder', !label || label === '—');
  }
  if (hintEl) hintEl.textContent = `驱动标识: ${cur} · 自动查套餐: ${getAutoPlanCheckValue() ? '开' : '关'} · 协议指纹: ${getProtocolFingerprintReuseValue() ? '同邮箱保持' : '每次新建'}`;
}
function renderRegistrationDriverField(f) {
  return `
    <label class="fld config-select-v2-field">
      注册方式
      <span class="hint">选择注册所用的自动化方式</span>
      <div class="config-ep-select" id="configRegistrationSelectV2"></div>
    </label>
  `;
}
function renderRegistrationSettingsSection(fields) {
  const driver = fields.find(f => f.key === 'REGISTRATION_DRIVER') || fields[0];
  const autoPlan = fields.find(f => f.key === 'AUTO_PLAN_CHECK_AFTER_REGISTER');
  const reuseFingerprint = fields.find(f => f.key === 'PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL');
  return `
    <div class="config-section-v2-body config-section-v2-body--single">
      ${driver ? renderRegistrationDriverField(driver) : '<div class="banner warn">未找到注册方式配置项</div>'}
    </div>
    ${(autoPlan || reuseFingerprint) ? `<div class="config-switches-v2" style="margin-top:14px;">
      ${autoPlan ? renderFeatureSwitchField(autoPlan, { withIcon: false }) : ''}
      ${reuseFingerprint ? renderFeatureSwitchField(reuseFingerprint, { withIcon: false }) : ''}
    </div>` : ''}
  `;
}
function renderFeatureSwitchField(f, opts = {}) {
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  const on = !!fv;
  const withIcon = opts.withIcon !== false;
  const icon = withIcon ? configFeatureIcon(f.key) : null;
  return `
    <div class="config-switch-v2">
      ${withIcon ? `
      <div class="config-switch-v2-top">
        <span class="config-switch-v2-icon ${icon.cls}" aria-hidden="true">
          <svg viewBox="0 0 24 24">${icon.svg}</svg>
        </span>
        <div class="config-switch-v2-label">${esc(f.label)}</div>
      </div>` : `<div class="config-switch-v2-label">${esc(f.label)}</div>`}
      <input class="config-switch-v2-toggle" type="checkbox" role="switch" data-key="${attrEsc(f.key)}"${on ? ' checked' : ''} aria-label="${attrEsc(f.label)}">
      <p class="config-switch-v2-help">${esc(f.help || '')}</p>
    </div>
  `;
}
function renderConfigExternalLink(f) {
  const url = String(f.external_url || '').trim();
  if (!/^https?:\/\//i.test(url)) return '';
  const label = String(f.external_label || '打开官网');
  return `<a class="config-external-link" href="${attrEsc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
}
function renderConfigRecommendedLinks(f) {
  const links = Array.isArray(f.recommended_links) ? f.recommended_links : [];
  const items = links.map(item => {
    const url = String(item?.url || '').trim();
    if (!/^https?:\/\//i.test(url)) return '';
    const label = String(item?.label || url);
    const description = String(item?.description || '').trim();
    const descriptionLinkUrl = String(item?.description_link_url || '').trim();
    const descriptionLinkLabel = String(item?.description_link_label || '').trim();
    const descriptionAfterLink = String(item?.description_after_link || '');
    const descriptionHtml = description
      ? `：${esc(description)}${/^https?:\/\//i.test(descriptionLinkUrl) && descriptionLinkLabel ? `<a href="${attrEsc(descriptionLinkUrl)}" target="_blank" rel="noopener noreferrer">${esc(descriptionLinkLabel)}</a>${esc(descriptionAfterLink)}` : ''}`
      : '';
    return `<span class="config-recommendations-item"><a href="${attrEsc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>${descriptionHtml}</span>`;
  }).filter(Boolean).join('');
  if (!items) return '';
  return `<div class="config-recommendations" role="note"><span class="config-recommendations-title">推荐家宽</span>${items}</div>`;
}
const SMS_PROVIDER_CHAIN_OPTIONS = [
  { value: 'smsbower', label: 'SMSBower' },
  { value: 'tiger', label: 'Tiger SMS' },
  { value: 'herosms', label: 'HeroSMS' },
  { value: 'smspool', label: 'SMSPool' },
  { value: 'vaksms', label: 'Vak SMS' },
];
function parseSmsProviderChain(value) {
  const allowed = new Set(SMS_PROVIDER_CHAIN_OPTIONS.map(item => item.value));
  const result = [];
  String(value || '').split(',').forEach(item => {
    const provider = item.trim().toLowerCase();
    if (allowed.has(provider) && !result.includes(provider)) result.push(provider);
  });
  return result;
}
function smsProviderChainLabel(value) {
  const item = SMS_PROVIDER_CHAIN_OPTIONS.find(option => option.value === value);
  return item ? item.label : value;
}
function smsProviderChainFieldValue(f) {
  const pending = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key)
    ? CONFIG_PENDING_UPDATES[f.key]
    : f.value;
  const configured = parseSmsProviderChain(pending);
  if (configured.length) return configured;
  const primaryField = (CONFIG || []).find(item => item.key === 'SMS_PROVIDER');
  const primaryValue = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, 'SMS_PROVIDER')
    ? CONFIG_PENDING_UPDATES.SMS_PROVIDER
    : (primaryField ? primaryField.value : 'smsbower');
  const primary = parseSmsProviderChain(primaryValue)[0] || 'smsbower';
  return [primary].concat(SMS_PROVIDER_CHAIN_OPTIONS.map(item => item.value).filter(value => value !== primary));
}
function renderSmsProviderChainOrder(chain) {
  const enabled = new Set(chain);
  const ordered = chain.concat(SMS_PROVIDER_CHAIN_OPTIONS.map(item => item.value).filter(value => !enabled.has(value)));
  return ordered.map(value => {
    const index = chain.indexOf(value);
    const active = index >= 0;
    const upDisabled = !active || index === 0;
    const downDisabled = !active || index === chain.length - 1;
    const up = '<button type="button" data-provider-chain-move="up" data-provider-chain-value="' + attrEsc(value) + '" aria-label="上移 ' + attrEsc(smsProviderChainLabel(value)) + '" title="上移"' + (upDisabled ? ' disabled' : '') + '>&#8593;</button>';
    const down = '<button type="button" data-provider-chain-move="down" data-provider-chain-value="' + attrEsc(value) + '" aria-label="下移 ' + attrEsc(smsProviderChainLabel(value)) + '" title="下移"' + (downDisabled ? ' disabled' : '') + '>&#8595;</button>';
    const priority = active ? '优先级 ' + String(index + 1).padStart(2, '0') : '未启用';
    return '<div class="config-provider-chain-row' + (active ? ' is-enabled' : '') + '">'
      + '<label class="config-provider-chain-choice">'
      + '<input type="checkbox" data-provider-chain-option="' + attrEsc(value) + '"' + (active ? ' checked' : '') + '>'
      + '<span>' + esc(smsProviderChainLabel(value)) + '</span></label>'
      + '<span class="config-provider-chain-priority">' + esc(priority) + '</span>'
      + '<span class="config-provider-chain-actions">' + up + down + '</span></div>';
  }).join('');
}
function smsProviderChainStoredValue(f) {
  const value = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key)
    ? CONFIG_PENDING_UPDATES[f.key]
    : f.value;
  return parseSmsProviderChain(value).join(',');
}
function renderSmsProviderChainField(f) {
  const chain = smsProviderChainFieldValue(f);
  return '<div class="fld config-provider-chain-field">'
    + '<div class="config-provider-chain-heading">'
    + '<div class="config-provider-chain-title">' + esc(f.label) + '</div>'
    + '<span class="hint">' + esc(f.help || '') + '</span>'
    + '</div>'
    + '<div class="config-provider-chain" data-config-provider-chain>'
    + '<div class="config-provider-chain-order" data-provider-chain-order>' + renderSmsProviderChainOrder(chain) + '</div>'
    + '<input type="hidden" data-key="' + attrEsc(f.key) + '" value="' + attrEsc(smsProviderChainStoredValue(f)) + '">'
    + '</div></div>';
}
function smsProviderChainCurrentValue(root) {
  const hidden = root.querySelector('input[data-key="SMS_PROVIDER_CHAIN"]');
  return smsProviderChainFieldValue({
    key: 'SMS_PROVIDER_CHAIN',
    value: hidden ? hidden.value : '',
  });
}
function setSmsProviderChainValue(root, chain) {
  const normalized = parseSmsProviderChain(chain.join(','));
  const hidden = root.querySelector('input[data-key="SMS_PROVIDER_CHAIN"]');
  if (!hidden) return;
  hidden.value = normalized.join(',');
  CONFIG_PENDING_UPDATES.SMS_PROVIDER_CHAIN = hidden.value;
  root.querySelectorAll('[data-provider-chain-option]').forEach(input => {
    input.checked = normalized.includes(input.dataset.providerChainOption);
  });
  const order = root.querySelector('[data-provider-chain-order]');
  if (order) order.innerHTML = renderSmsProviderChainOrder(normalized);
}
function updateSmsProviderChainFromCheckbox(input) {
  const root = input.closest('[data-config-provider-chain]');
  if (!root) return;
  const current = smsProviderChainCurrentValue(root);
  const value = input.dataset.providerChainOption;
  const next = input.checked
    ? (current.includes(value) ? current : current.concat(value))
    : current.filter(item => item !== value);
  if (!next.length) {
    input.checked = true;
    showToast('至少保留一个短信平台');
    return;
  }
  setSmsProviderChainValue(root, next);
}
function moveSmsProviderChain(value, direction, root) {
  const chain = smsProviderChainCurrentValue(root);
  const index = chain.indexOf(value);
  const target = direction === 'up' ? index - 1 : index + 1;
  if (index < 0 || target < 0 || target >= chain.length) return;
  [chain[index], chain[target]] = [chain[target], chain[index]];
  setSmsProviderChainValue(root, chain);
}
function renderConfigPlainFieldV2(f) {
  if (f.key === 'SMS_PROVIDER_CHAIN') return renderSmsProviderChainField(f);
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  let control = '';
  if (Array.isArray(f.choices) && f.choices.length) {
    control = `<select data-key="${attrEsc(f.key)}">${f.choices.map(item => `<option value="${attrEsc(item.value)}"${String(fv) === String(item.value) ? ' selected' : ''}>${esc(item.label || item.value)}</option>`).join('')}</select>`;
  } else if (f.type === 'int') {
    control = `<input type="number" data-key="${attrEsc(f.key)}" value="${attrEsc(fv == null ? '' : fv)}">`;
  } else if (f.type === 'float') {
    control = `<input type="number" step="0.1" data-key="${attrEsc(f.key)}" value="${attrEsc(fv == null ? '' : fv)}">`;
  } else if (f.type === 'list_str_multiline') {
    control = `<textarea data-key="${attrEsc(f.key)}" placeholder="每行一条，可留空">${attrEsc((fv || []).join('\n'))}</textarea>`;
  } else {
    const shown = isPlaceholderEmpty(fv) ? '' : fv;
    const inputType = f.secret ? 'password' : 'text';
    const ph = f.secret ? '保存在 .env，可留空' : '可留空';
    control = `<input type="${inputType}" data-key="${attrEsc(f.key)}" value="${attrEsc(shown)}" placeholder="${ph}" autocomplete="off" spellcheck="false">`;
  }
  const field = `
    <label class="fld">
      ${esc(f.label)}
      <span class="hint">${esc(f.help || '')}</span>
      ${control}
    </label>
  `;
  const decorated = f.external_url
    ? `<div class="config-field-wrap">${field}${renderConfigExternalLink(f)}</div>`
    : field;
  return decorated + renderConfigRecommendedLinks(f);
}
function renderMixedConfigSectionV2(name, fields, intro) {
  const slug = configGroupSlug(name);
  const switches = fields.filter(f => f.type === 'bool');
  const inputs = fields.filter(f => f.type !== 'bool');
  const extra = name === 'RoxyBrowser' ? renderRoxyWorkspaceToolsV2() : '';
  return `
    <section class="config-section-v2" id="${esc(slug)}" data-config-section="${esc(name)}">
      ${renderConfigSectionHead(name, intro || '')}
      ${extra}
      ${switches.length ? `<div class="config-switches-v2">${switches.map(f => renderFeatureSwitchField(f, { withIcon: false })).join('')}</div>` : ''}
      ${inputs.length ? `<div class="config-section-v2-body">${inputs.map(renderConfigPlainFieldV2).join('')}</div>` : ''}
      <div class="config-section-v2-actions">
        <button type="button" class="btn primary" data-save-config-v2>保存</button>
      </div>
    </section>
  `;
}

function codexOauthDriverChoices() {
  return [
    { value: 'protocol', label: '纯协议授权' },
    { value: 'roxy', label: 'RoxyBrowser' },
    { value: 'cloak', label: '本地指纹浏览器' },
    { value: 'browser_use', label: 'browser_use' },
    { value: 'skyvern', label: 'skyvern' },
    { value: 'same_as_registration', label: '跟随注册驱动' },
  ];
}
function getCodexOauthDriverValue() {
  const f = (CONFIG || []).find(x => x.key === 'CODEX_OAUTH_DRIVER');
  if (!f) return 'protocol';
  const fv = Object.prototype.hasOwnProperty.call(CONFIG_PENDING_UPDATES, f.key) ? CONFIG_PENDING_UPDATES[f.key] : f.value;
  return String(fv == null ? 'protocol' : fv).trim().toLowerCase() || 'protocol';
}
function codexOauthDriverLabel(value) {
  const cur = String(value || '').trim().toLowerCase();
  const hit = codexOauthDriverChoices().find(c => String(c.value) === cur);
  return hit ? String(hit.label || hit.value) : (cur || '—');
}
function setCodexOauthDriverValue(value) {
  const cur = String(value || 'protocol').trim().toLowerCase() || 'protocol';
  CONFIG_PENDING_UPDATES.CODEX_OAUTH_DRIVER = cur;
  closeConfigEpSelects();
  renderConfigLayoutV2();
}
function renderCodexOauthDriverField() {
  const cur = getCodexOauthDriverValue();
  const label = codexOauthDriverLabel(cur);
  const wrapId = 'configCodexOauthSelectV2';
  const items = codexOauthDriverChoices().map(c => `
    <button type="button" class="config-ep-select-item${c.value === cur ? ' is-active' : ''}" role="option" data-codex-oauth-value="${attrEsc(c.value)}">${esc(c.label)}</button>
  `).join('');
  return `
    <label class="fld config-select-v2-field">
      Codex 授权驱动
      <span class="hint">选择 Codex 授权所用的自动化方式</span>
      <div class="config-ep-select" id="${wrapId}">
        <button type="button" class="config-ep-select-btn" data-ep-toggle="${wrapId}">${esc(label)}</button>
        <div class="config-ep-select-menu" role="listbox">${items}</div>
        <input type="hidden" data-key="CODEX_OAUTH_DRIVER" value="${attrEsc(cur)}">
      </div>
    </label>
  `;
}
function renderCodexFieldV2(f) {
  if (f.key === 'CODEX_OAUTH_DRIVER') return renderCodexOauthDriverField();
  if (f.type === 'bool') return renderFeatureSwitchField(f, { withIcon: false });
  return renderConfigPlainFieldV2(f);
}

function renderSectionedConfigSectionV2(name, fields, sectionForKey, preferred, activeRef, dataAttr) {
  const slug = configGroupSlug(name);
  const bySection = {};
  for (const f of fields) {
    const [section, help] = sectionForKey(f.key || '');
    if (!bySection[section]) bySection[section] = { help, fields: [] };
    bySection[section].fields.push(f);
  }
  const sections = preferred.filter(x => bySection[x]).concat(Object.keys(bySection).filter(x => !preferred.includes(x)));
  let active = activeRef.get();
  if (!sections.includes(active)) {
    active = sections[0] || '';
    activeRef.set(active);
  }
  const current = bySection[active] || { help: '', fields: [] };
  const switches = current.fields.filter(f => f.type === 'bool');
  const inputs = current.fields.filter(f => f.type !== 'bool');
  const promo = name === '邮箱 / OTP' && active === 'Remail'
    ? '<span class="remail-promo-notice" role="note">通过作者卡网购买积分9折优惠</span>'
    : '';
  const sectionHelp = current.help
    ? `<p class="config-section-v2-subhelp">${esc(current.help)}${promo}</p>`
    : promo;
  let extra = '';
  if (name === '邮箱 / OTP' && active === 'CloudMail') {
    extra = renderCloudMailTokenToolsV2();
  }
  const sectionHtml = `
    <section class="config-section-v2" id="${esc(slug)}" data-config-section="${esc(name)}">
      ${renderConfigSectionHead(name, '')}
      <div class="config-subtabs-v2">
        ${sections.map(section => `
          <button type="button" data-${dataAttr}="${esc(section)}" class="${section === active ? 'active' : ''}">
            ${esc(section)}
          </button>
        `).join('')}
      </div>
      ${sectionHelp}
      ${switches.length ? `<div class="config-switches-v2">${switches.map(f => renderFeatureSwitchField(f, { withIcon: false })).join('')}</div>` : ''}
      ${inputs.length ? `<div class="config-section-v2-body">${inputs.map(renderConfigPlainFieldV2).join('')}</div>` : ''}
      ${extra}
      <div class="config-section-v2-actions">
        <button type="button" class="btn primary" data-save-config-v2>保存</button>
      </div>
    </section>
  `;
  return sectionHtml;
}
function renderCloudMailTokenToolsV2() {
  const hasCloudMail = CONFIG.some(f => String(f.key || '').startsWith('CLOUDMAIL_'));
  if (!hasCloudMail) return '';
  return `
    <div class="roxy-workspace-box" style="margin-top:18px;">
      <div>
        <b>CloudMail Token</b>
        <div class="hint">填写 API 地址、管理员邮箱、密码后，生成 Token；保存配置不会自动生成。</div>
      </div>
      <div class="action-cell">
        <button class="btn primary" type="button" id="btnGenCloudMailTokenV2">生成 CloudMail Token</button>
        <button class="btn" type="button" id="btnLoadCloudMailDomainsV2">获取 CloudMail 域名</button>
      </div>
      <div id="cloudMailTokenStatusV2" class="muted" style="font-size:12px;">未生成</div>
    </div>
  `;
}
function bindCloudMailToolsV2() {
  const genBtn = $('#btnGenCloudMailTokenV2');
  const domainBtn = $('#btnLoadCloudMailDomainsV2');
  if (genBtn && !genBtn.dataset.bound) {
    genBtn.dataset.bound = '1';
    genBtn.addEventListener('click', () => genCloudMailTokenV2());
  }
  if (domainBtn && !domainBtn.dataset.bound) {
    domainBtn.dataset.bound = '1';
    domainBtn.addEventListener('click', () => loadCloudMailDomainsV2());
  }
}
function _configValueV2(key) {
  const root = document.querySelector(`#configSectionsV2 [data-key="${CSS.escape(key)}"]`);
  if (root) return root.value;
  const f = CONFIG.find(x => x.key === key);
  return f ? String(f.value ?? '') : '';
}
async function genCloudMailTokenV2() {
  const btn = $('#btnGenCloudMailTokenV2');
  const status = $('#cloudMailTokenStatusV2');
  if (!btn || !status) return;
  btn.disabled = true;
  status.textContent = '正在调用 CloudMail /api/public/genToken...';
  try {
    const r = await api('/api/cloudmail/gen-token', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        api_base: _configValueV2('CLOUDMAIL_API_BASE').trim(),
        admin_email: _configValueV2('CLOUDMAIL_ADMIN_EMAIL').trim(),
        password: _configValueV2('CLOUDMAIL_PASSWORD').trim(),
        path: _configValueV2('CLOUDMAIL_TOKEN_PATH').trim() || '/api/public/genToken',
      }),
    });
    status.innerHTML = `✅ ${esc(r.message || 'Token 已生成并保存')}`;
    showToast('CloudMail Token 已生成');
    await loadConfig();
  } catch(e) {
    status.innerHTML = `<span style="color:var(--red);">生成失败：${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}
async function loadCloudMailDomainsV2() {
  const btn = $('#btnLoadCloudMailDomainsV2');
  const status = $('#cloudMailTokenStatusV2');
  if (!btn || !status) return;
  btn.disabled = true;
  status.textContent = '正在从 CloudMail 平台获取可用域名...';
  try {
    const r = await api('/api/cloudmail/domains', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        api_base: _configValueV2('CLOUDMAIL_API_BASE').trim(),
        admin_email: _configValueV2('CLOUDMAIL_ADMIN_EMAIL').trim(),
        password: _configValueV2('CLOUDMAIL_PASSWORD').trim(),
        token: _configValueV2('CLOUDMAIL_AUTH_TOKEN').trim(),
      }),
    });
    const domains = Array.isArray(r.domains) ? r.domains : [];
    status.innerHTML = `✅ ${esc(r.message || '域名已获取')}：<span class="mono">${esc(domains.join(', '))}</span>`;
    showToast(`CloudMail 已获取 ${domains.length} 个域名`);
    await loadConfig();
  } catch(e) {
    status.innerHTML = `<span style="color:var(--red);">获取域名失败：${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

function renderCodexSectionV2(fields) {
  const slug = configGroupSlug('Codex');
  const preferred = ['基础配置', 'CPA配置', 'sub2api通用', 'sub2api Agent Token'];
  const bySection = {};
  for (const f of fields) {
    const [section, help] = codexConfigSectionForKey(f.key || '');
    if (!bySection[section]) bySection[section] = { help, fields: [] };
    bySection[section].fields.push(f);
  }
  const sections = preferred.filter(x => bySection[x]).concat(Object.keys(bySection).filter(x => !preferred.includes(x)));
  if (!sections.includes(CONFIG_CODEX_ACTIVE_SECTION_V2)) {
    CONFIG_CODEX_ACTIVE_SECTION_V2 = sections[0] || '基础配置';
  }
  const active = CONFIG_CODEX_ACTIVE_SECTION_V2;
  const current = bySection[active] || { help: '', fields: [] };
  const switches = current.fields.filter(f => f.type === 'bool' && f.key !== 'CODEX_OAUTH_DRIVER');
  const inputs = current.fields.filter(f => f.type !== 'bool' || f.key === 'CODEX_OAUTH_DRIVER');
  // Keep driver in inputs area first
  const orderedInputs = [];
  const driver = current.fields.find(f => f.key === 'CODEX_OAUTH_DRIVER');
  if (driver) orderedInputs.push(driver);
  current.fields.forEach(f => {
    if (f.key === 'CODEX_OAUTH_DRIVER') return;
    if (f.type === 'bool') return;
    orderedInputs.push(f);
  });
  return `
    <section class="config-section-v2" id="${esc(slug)}" data-config-section="Codex">
      ${renderConfigSectionHead('Codex', '')}
      <div class="config-subtabs-v2">
        ${sections.map(section => `
          <button type="button" data-codex-section-v2="${esc(section)}" class="${section === active ? 'active' : ''}">
            ${esc(section)}
          </button>
        `).join('')}
      </div>
      ${current.help ? `<p class="config-section-v2-subhelp">${esc(current.help)}</p>` : ''}
      ${switches.length ? `<div class="config-switches-v2">${switches.map(f => renderFeatureSwitchField(f, { withIcon: false })).join('')}</div>` : ''}
      ${orderedInputs.length ? `<div class="config-section-v2-body">${orderedInputs.map(renderCodexFieldV2).join('')}</div>` : ''}
      <div class="config-section-v2-actions">
        <button type="button" class="btn primary" data-save-config-v2>保存</button>
      </div>
    </section>
  `;
}

function renderConfigSectionV2(name, fields) {
  const slug = configGroupSlug(name);
  if (name === '网站配置') {
    return `
      <section class="config-section-v2" id="${esc(slug)}" data-config-section="${esc(name)}">
        ${renderConfigSectionHead(name, configSectionIntro(name))}
        <div class="config-section-v2-body">
          ${fields.map(renderConfigPlainFieldV2).join('')}
        </div>
        <div class="config-section-v2-actions">
          <button type="button" class="btn primary" data-save-config-v2>保存</button>
        </div>
      </section>
    `;
  }
  if (name === '功能开关') {
    const preferred = ['ENABLE_CODEX_AUTO', 'ENABLE_2FA', 'ENABLE_FLOW_TRIGGER'];
    const ordered = preferred
      .map(k => fields.find(f => f.key === k))
      .filter(Boolean)
      .concat(fields.filter(f => !preferred.includes(f.key)));
    const switches = ordered.filter(f => f.type === 'bool');
    const inputs = ordered.filter(f => f.type !== 'bool');
    return `
      <section class="config-section-v2" id="${esc(slug)}" data-config-section="${esc(name)}">
        ${renderConfigSectionHead(name, '')}
        <div class="config-switches-v2">
          ${switches.map(f => renderFeatureSwitchField(f, { withIcon: true })).join('')}
        </div>
        ${inputs.length ? `<div class="config-section-v2-body">${inputs.map(renderConfigPlainFieldV2).join('')}</div>` : ''}
        <div class="config-section-v2-actions">
          <button type="button" class="btn primary" data-save-config-v2>保存</button>
        </div>
      </section>
    `;
  }
  if (name === '注册方式') {
    return `
      <section class="config-section-v2" id="${esc(slug)}" data-config-section="${esc(name)}">
        ${renderConfigSectionHead(name, configSectionIntro(name))}
        ${renderRegistrationSettingsSection(fields)}
        <div class="config-section-v2-actions">
          <button type="button" class="btn primary" data-save-config-v2>保存</button>
        </div>
      </section>
    `;
  }
  if (name === 'Codex') {
    return renderCodexSectionV2(fields);
  }
  if (name === '邮箱 / OTP') {
    return renderSectionedConfigSectionV2(
      name,
      fields,
      emailConfigSectionForKey,
      ['通用邮箱 / OTP', 'GPTMail', 'MailNest', 'CloudMail', 'Remail', 'Outlook 邮箱池', 'Cloudflare 临时邮箱', 'Cloudflare 域名邮箱', '其他邮箱配置'],
      { get: () => CONFIG_EMAIL_ACTIVE_SECTION_V2, set: v => { CONFIG_EMAIL_ACTIVE_SECTION_V2 = v; } },
      'email-section-v2'
    );
  }
  if (name === '接码平台') {
    return renderSectionedConfigSectionV2(
      name,
      fields,
      smsConfigSectionForKey,
      ['通用接码', '低价号码池', 'SMSBower', 'Tiger SMS', 'HeroSMS', 'SMSPool', 'Vak SMS'],
      { get: () => CONFIG_SMS_ACTIVE_SECTION_V2, set: v => { CONFIG_SMS_ACTIVE_SECTION_V2 = v; } },
      'sms-section-v2'
    );
  }
  return renderMixedConfigSectionV2(name, fields, configSectionIntro(name));
}
function renderConfigLayoutV2() {
  const nav = document.getElementById('configNavV2');
  const sections = document.getElementById('configSectionsV2');
  if (!nav || !sections) return;
  const groups = configGroups();
  const names = Object.keys(groups);
  nav.innerHTML = names.map((name, i) => `
    <button type="button" class="config-nav-v2-item${i === 0 ? ' is-active' : ''}" data-config-nav="${esc(name)}" data-config-target="${esc(configGroupSlug(name))}">
      <svg viewBox="0 0 24 24" aria-hidden="true">${configNavIcon(name)}</svg>
      <span>${esc(configGroupDisplayName(name))}</span>
    </button>
  `).join('');
  sections.innerHTML = names.map(name => renderConfigSectionV2(name, groups[name] || [])).join('');
  renderRegistrationDriverCard();
  const sectionMount = document.getElementById('configRegistrationSelectV2');
  if (sectionMount) renderRegistrationEpSelect(sectionMount, 'configRegistrationSelectV2');
  bindRoxyWorkspaceToolsV2();
  bindCloudMailToolsV2();
}
function scrollToConfigSection(slug) {
  const el = document.getElementById(slug);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function setActiveConfigNav(slug) {
  document.querySelectorAll('#configNavV2 .config-nav-v2-item').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.configTarget === slug);
  });
}
function bindConfigLayoutV2() {
  const nav = document.getElementById('configNavV2');
  if (!nav || nav.dataset.bound) return;
  nav.dataset.bound = '1';
  nav.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-config-nav]');
    if (!btn) return;
    const slug = btn.dataset.configTarget;
    setActiveConfigNav(slug);
    scrollToConfigSection(slug);
  });
  const layout = document.getElementById('configLayoutV2');
  if (layout && !layout.dataset.epBound) {
    layout.dataset.epBound = '1';
    layout.addEventListener('click', (e) => {
      const chainMove = e.target.closest('[data-provider-chain-move]');
      if (chainMove) {
        e.preventDefault();
        e.stopPropagation();
        const root = chainMove.closest('[data-config-provider-chain]');
        moveSmsProviderChain(chainMove.dataset.providerChainValue, chainMove.dataset.providerChainMove, root);
        return;
      }
      const codexTab = e.target.closest('[data-codex-section-v2]');
      if (codexTab) {
        e.preventDefault();
        CONFIG_CODEX_ACTIVE_SECTION_V2 = codexTab.dataset.codexSectionV2;
        renderConfigLayoutV2();
        return;
      }
      const emailTab = e.target.closest('[data-email-section-v2]');
      if (emailTab) {
        e.preventDefault();
        CONFIG_EMAIL_ACTIVE_SECTION_V2 = emailTab.dataset.emailSectionV2;
        renderConfigLayoutV2();
        return;
      }
      const smsTab = e.target.closest('[data-sms-section-v2]');
      if (smsTab) {
        e.preventDefault();
        CONFIG_SMS_ACTIVE_SECTION_V2 = smsTab.dataset.smsSectionV2;
        renderConfigLayoutV2();
        return;
      }
      const toggle = e.target.closest('[data-ep-toggle]');
      if (toggle) {
        toggleConfigEpSelect(toggle.dataset.epToggle, e);
        return;
      }
      const item = e.target.closest('[data-driver-value]');
      if (item) {
        e.preventDefault();
        e.stopPropagation();
        setRegistrationDriverValue(item.dataset.driverValue);
        return;
      }
      const oauthItem = e.target.closest('[data-codex-oauth-value]');
      if (oauthItem) {
        e.preventDefault();
        e.stopPropagation();
        setCodexOauthDriverValue(oauthItem.dataset.codexOauthValue);
        return;
      }
    });
    layout.addEventListener('change', (e) => {
      const input = e.target.closest('[data-provider-chain-option]');
      if (input) updateSmsProviderChainFromCheckbox(input);
    });
  }
  if (!document.documentElement.dataset.configEpCloseBound) {
    document.documentElement.dataset.configEpCloseBound = '1';
    document.addEventListener('click', () => closeConfigEpSelects());
  }
  const sectionRoot = document.getElementById('configSectionsV2');
  if (sectionRoot && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      const visible = entries
        .filter(x => x.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible || !visible.target.id) return;
      setActiveConfigNav(visible.target.id);
    }, { rootMargin: '-20% 0px -60% 0px', threshold: [0.15, 0.35, 0.6] });
    const watch = () => {
      sectionRoot.querySelectorAll('.config-section-v2').forEach(sec => io.observe(sec));
    };
    watch();
    const mo = new MutationObserver(watch);
    mo.observe(sectionRoot, { childList: true });
  }
}

async function loadConfig() {
  try {
    CONFIG = await api('/api/config');
    renderConfigLayoutV2();
    bindConfigLayoutV2();
  } catch(e) {
    showToast('加载配置失败: ' + e.message);
  }
}

async function loadRoxyWorkspaces() {
  const status = $('#roxyWorkspaceStatusV2');
  const sel = $('#roxyWorkspaceSelectV2');
  const saveBtn = $('#btnSaveRoxyWorkspaceV2');
  const loadBtn = $('#btnLoadRoxyWorkspacesV2');
  if (!status || !sel) return;
  if (loadBtn) loadBtn.disabled = true;
  if (saveBtn) saveBtn.disabled = true;
  status.textContent = '正在调用 Roxy API 获取团队/工作区...';
  try {
    const r = await api('/api/roxy/workspaces');
    const items = r.items || [];
    if (!items.length) {
      const errs = (r.errors || []).slice(0, 5).map(x => `${x.method || ''} ${x.path || ''}: ${x.error || ''}`).join('；');
      throw new Error(errs ? `未返回团队/工作区列表。探测结果：${errs}` : '未返回团队/工作区列表');
    }
    const current = (CONFIG.find(f => f.key === 'ROXY_WORKSPACE_ID') || {}).value || '';
    const currentProject = (CONFIG.find(f => f.key === 'ROXY_PROJECT_ID') || {}).value || '';
    sel.innerHTML = `<option value="">请选择团队/项目（当前团队：${esc(current || '未设置')}）</option>` +
      items.map(x => {
        const value = `${x.id}::${x.projectId || ''}`;
        const selected = String(x.id)===String(current) && String(x.projectId || '')===String(currentProject);
        return `<option value="${esc(value)}" data-workspace-id="${esc(x.id)}" data-project-id="${esc(x.projectId || '')}"${selected?' selected':''}>${esc(x.label || (x.name + ' (' + x.id + ')'))}</option>`;
      }).join('');
    if (saveBtn) saveBtn.disabled = !sel.value;
    status.innerHTML = `已获取 ${items.length} 个团队/项目，接口：<span class="mono">${esc(r.method || '')} ${esc(r.path || '')}</span>`;
  } catch(e) {
    status.innerHTML = `<span style="color:var(--red);">获取失败：${esc(e.message)}</span>`;
  } finally {
    if (loadBtn) loadBtn.disabled = false;
  }
}

async function saveRoxyWorkspaceSelection() {
  const sel = $('#roxyWorkspaceSelectV2');
  const status = $('#roxyWorkspaceStatusV2');
  const opt = sel ? sel.selectedOptions[0] : null;
  const workspaceId = opt ? (opt.dataset.workspaceId || '') : '';
  const projectId = opt ? (opt.dataset.projectId || '') : '';
  if (!workspaceId) return;
  try {
    const r = await api('/api/config', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({updates: {ROXY_WORKSPACE_ID: workspaceId, ROXY_PROJECT_ID: projectId}}),
    });
    const fw = CONFIG.find(x => x.key === 'ROXY_WORKSPACE_ID');
    const fp = CONFIG.find(x => x.key === 'ROXY_PROJECT_ID');
    if (fw) fw.value = workspaceId;
    if (fp) fp.value = projectId;
    if (status) status.innerHTML = `✅ 已保存团队 ID：<span class="mono">${esc(workspaceId)}</span>，项目 ID：<span class="mono">${esc(projectId || '未设置')}</span>`;
    showToast(r.reloaded ? 'Roxy 团队/项目已保存并生效' : 'Roxy 团队/项目已保存');
    renderConfigLayoutV2();
  } catch(e) {
    if (status) status.innerHTML = `<span style="color:var(--red);">保存失败：${esc(e.message)}</span>`;
  }
}


function readConfigElementValue(el, f) {
  if (!f) return undefined;
  if (f.type === 'list_str_multiline') return el.value.split('\n').map(s=>s.trim()).filter(Boolean);
  if (f.type === 'bool') {
    if (el.type === 'checkbox') return !!el.checked;
    return el.value === 'true';
  }
  if (f.type === 'int') {
    const value = parseInt(String(el.value || '').trim(), 10);
    return Number.isFinite(value) ? value : Number(f.value || 0);
  }
  if (f.type === 'float') {
    const value = parseFloat(String(el.value || '').trim());
    return Number.isFinite(value) ? value : Number(f.value || 0);
  }
  return isPlaceholderEmpty(el.value) ? '' : el.value.trim();
}

function trackConfigFieldChange(e) {
  const el = e.target.closest('[data-key]');
  if (!el || !el.closest('#tab-config')) return;
  const f = CONFIG.find(x => x.key === el.dataset.key);
  if (!f) return;
  CONFIG_PENDING_UPDATES[f.key] = readConfigElementValue(el, f);
  if (f.key === 'AUTO_PLAN_CHECK_AFTER_REGISTER' || f.key === 'PROTOCOL_REUSE_FINGERPRINT_BY_EMAIL') renderRegistrationDriverCard();
}
$('#tab-config').addEventListener('input', trackConfigFieldChange);
$('#tab-config').addEventListener('change', trackConfigFieldChange);

async function saveConfigUpdates(triggerBtn) {
  const updates = {...CONFIG_PENDING_UPDATES};
  const seen = new Set();
  $$('#configCardsV2 [data-key], #configSectionsV2 [data-key]').forEach(el => {
    const f = CONFIG.find(x => x.key === el.dataset.key);
    if (!f || seen.has(f.key)) return;
    seen.add(f.key);
    updates[f.key] = readConfigElementValue(el, f);
  });
  const buttons = [triggerBtn, ...$$('#tab-config [data-save-config-v2]')].filter(Boolean);
  buttons.forEach(btn => { btn.disabled = true; });
  try {
    const r = await api('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({updates}) });
    for (const [k, v] of Object.entries(updates)) { const f = CONFIG.find(x => x.key === k); if (f) f.value = v; delete CONFIG_PENDING_UPDATES[k]; }
    renderConfigLayoutV2();
    showToast(r.reloaded ? '配置已生效' : '配置已保存（需重启）');
  } catch(e) { showToast('保存失败: ' + e.message); }
  finally { buttons.forEach(btn => { btn.disabled = false; }); }
}
$('#tab-config').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-save-config-v2]');
  if (!btn) return;
  saveConfigUpdates(btn);
});

// ---------- 全局复制委托 ----------
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-copy-id]');
  if (!t) return;
  copyText(copyStore.get(t.dataset.copyId));
});

// ---------- 初始化 ----------
loadSummary();
activateTab(localStorage.getItem('gpt_console_active_tab') || 'register', false);
jobsTimer = setInterval(() => { if (!$('#tab-register').classList.contains('hidden')) refreshJobs(); }, 10000);
setInterval(loadSummary, 30000);
setInterval(() => { if (!$('#tab-accounts').classList.contains('hidden')) pollAccountPlanStatuses(); }, 10000);

async function viewSmsPoolReservationLog(reservationId) {
  try {
    const data = await api('/api/sms-pool/reservations/' + encodeURIComponent(reservationId) + '/log?tail=600');
    const modal = document.createElement('div');
    modal.className = 'sms-pool-log-modal';
    modal.innerHTML = '<div class="sms-pool-log-dialog" role="dialog" aria-modal="true">'
      + '<div class="sms-pool-log-head"><strong>号码 #' + esc(reservationId) + ' 接码日志</strong>'
      + '<button type="button" class="sms-pool-log-close" aria-label="关闭">×</button></div>'
      + '<pre>' + esc(data.text || '暂无日志') + '</pre></div>';
    document.body.appendChild(modal);
    const close = () => modal.remove();
    modal.querySelector('.sms-pool-log-close').addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
  } catch (error) { showToast('日志读取失败：' + error.message); }
}
(function () {
  const box = smsPoolEl('smsPoolReservationsBox');
  if (!box) return;
  box.addEventListener('click', function (event) {
    const btn = event.target.closest('[data-sms-pool-reslog]');
    if (btn) viewSmsPoolReservationLog(btn.getAttribute('data-sms-pool-reslog'));
  });
})();

// 键盘快捷键：/ 聚焦账号搜索；Alt+R 刷新列表。在输入框里时不劫持。
document.addEventListener('keydown', (e) => {
  const tag = (e.target && e.target.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable);
  if (e.key === '/' && !typing) {
    const box = document.getElementById('qAccountsV2');
    if (box) { e.preventDefault(); box.focus(); box.select(); }
  }
  if (e.altKey && (e.key === 'r' || e.key === 'R')) {
    if (typeof loadAccounts === 'function') { e.preventDefault(); loadAccounts(); }
  }
});

// 注册漏斗：把 /api/funnel 的统计显示出来，并提供 Codex 批量补跑入口。
async function loadFunnelV2() {
  const card = $('#funnelCardV2');
  const stats = $('#funnelStatsV2');
  if (!card || !stats) return;
  try {
    const d = await api('/api/funnel?limit=500');
    if (!d || d.ok === false) { card.hidden = true; return; }
    const status = d.status || {};
    const sub = d.sub_status || {};
    const total = Number(d.total || 0) || 1;
    const pct = (n) => ((Number(n || 0) * 100) / total).toFixed(1) + '%';
    const parts = [
      '总计 ' + total,
      '成功 ' + (status.success || 0) + ' (' + pct(status.success) + ')',
      '部分成功 ' + (status.partial_success || 0) + ' (' + pct(status.partial_success) + ')',
      '失败 ' + (status.failed || 0),
      'Codex 未跑 ' + (sub['codex_status:skipped'] || 0) + ' / 失败 ' + (sub['codex_status:failed'] || 0),
    ];
    stats.textContent = parts.join(' · ');
    card.hidden = false;
  } catch (e) { card.hidden = true; }
}

document.addEventListener('click', async (event) => {
  const btn = event.target.closest('#btnCodexBackfillV2');
  if (!btn) return;
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '入队中…';
  try {
    const r = await api('/api/codex/backfill', {method: 'POST', body: JSON.stringify({limit: 10})});
    showToast('已入队 ' + ((r && r.queued) ? r.queued.length : 0) + ' 个，忙碌 ' + ((r && r.busy) || 0));
    loadFunnelV2();
  } catch (e) {
    showToast('补跑失败: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

async function loadRegSplitState(){
  const box = document.getElementById("regSplitV2");
  if (!box) return;
  try {
    const st = await api("/api/registration/switch");
    box.checked = !!(st && st.split);
  } catch (e) {}
  box.addEventListener("change", async () => {
    try {
      await api("/api/registration/switch", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({split: box.checked, reason: "ui"})});
      const w = document.getElementById("regWarnV2");
      if (w) w.innerHTML = box.checked
        ? '<div class="banner info">分裂注册已开启：每单会派生 +随机后缀 的子地址，邮箱可用量按倍数放大</div>'
        : '<div class="banner info">分裂注册已关闭</div>';
    } catch (e) {
      box.checked = !box.checked;
    }
  });
}
loadRegSplitState();
