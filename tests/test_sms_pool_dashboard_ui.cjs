const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const html = fs.readFileSync('webui/templates/index.html', 'utf8');
const start = html.indexOf('function smsPoolLivePill(item)');
const end = html.indexOf('async function loadSmsPoolStatus()', start);
assert.notEqual(start, -1, 'SMS pool status helpers exist');
assert.notEqual(end, -1, 'SMS pool status helper boundary exists');

const context = {
  smsPoolState: {requestSeq: {status: 0, eligible: 0, reservations: 0, events: 0}},
  smsPoolPill: (label, tone = '') => `${tone}:${label}`,
  smsPoolStatusNames: {},
  smsPoolTag: (status) => String(status || ''),
  smsPoolFmtTime: (value) => String(value || '—'),
  smsPoolMoney: (value) => String(value),
  smsPoolNotice: () => {},
  esc: (value) => String(value ?? ''),
};
const helpersAndDashboard = html.slice(start, html.indexOf('// ---------- 概览 ----------', end));
new vm.Script(helpersAndDashboard, {filename: 'sms-pool-dashboard.js'});
vm.runInNewContext(helpersAndDashboard, context);

test('SMS pool dashboard block parses as JavaScript', () => {
  assert.ok(helpersAndDashboard.length > 0);
});

test('malformed page values fall back instead of producing NaN offsets', () => {
  assert.equal(context.smsPoolPageValue('NaN'), 1);
  assert.equal(context.smsPoolPageValue('-3'), 1);
  assert.equal(context.smsPoolPageSizeValue('NaN'), 20);
  assert.equal(context.smsPoolPageCount('NaN', 20), 1);
});

test('failed liveness checks are not represented as confirmed deactivation', () => {
  const failed = context.smsPoolLivePill({live_check_status: 'failed'});
  const dead = context.smsPoolLivePill({live_check_status: 'deactivated'});
  assert.match(failed, /检查失败/);
  assert.match(failed, /未判定失效/);
  assert.doesNotMatch(failed, /已确认失效/);
  assert.match(dead, /已确认失效/);
});

test('request sequence guard recognizes stale responses', () => {
  const oldToken = context.smsPoolRequestToken('eligible');
  const newToken = context.smsPoolRequestToken('eligible');
  assert.equal(context.smsPoolRequestIsCurrent('eligible', oldToken), false);
  assert.equal(context.smsPoolRequestIsCurrent('eligible', newToken), true);
});

test('reservation pagination discards an older response', async () => {
  const ids = ['smsPoolReservationsBox', 'smsPoolReservationsSummary', 'smsPoolReservationsPageInfo', 'smsPoolReservationsPrev', 'smsPoolReservationsNext'];
  const elements = Object.fromEntries(ids.map((id) => [id, {innerHTML: '', textContent: '', disabled: false, setAttribute() {}}]));
  elements.smsPoolReservationsPageSize = {value: '20'};
  context.smsPoolEl = (id) => elements[id];
  context.smsPoolState = {
    reservations: [], reservationPage: 1, reservationPageSize: 20, reservationTotal: 0,
    reservationsLoaded: false, requestSeq: {status: 0, eligible: 0, reservations: 0, events: 0},
  };
  const resolvers = [];
  context.api = () => new Promise((resolve) => resolvers.push(resolve));
  const oldResponse = context.loadSmsPoolReservations();
  const currentResponse = context.loadSmsPoolReservations();
  resolvers[1]({items: [{id: 2, status: 'completed', phone: '5552'}], total: 1});
  await currentResponse;
  resolvers[0]({items: [{id: 1, status: 'completed', phone: '5551'}], total: 1});
  await oldResponse;
  assert.match(elements.smsPoolReservationsBox.innerHTML, /#2/);
  assert.doesNotMatch(elements.smsPoolReservationsBox.innerHTML, /#1/);
});
