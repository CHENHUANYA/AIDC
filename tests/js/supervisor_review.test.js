const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function classList() {
  const values = new Set();
  return {
    add: (value) => values.add(value),
    remove: (value) => values.delete(value),
    contains: (value) => values.has(value),
  };
}

function loadSupervisorPage({ orders = [], issues = [], mergedEvents = [] } = {}) {
  const state = new Map([
    ['supervisorOrders', orders],
    ['supervisorIssues', issues],
  ]);
  const closeButton = { focused: false, focus() { this.focused = true; } };
  const scroll = { scrollTop: 99 };
  const modal = {
    classList: classList(),
    querySelector: (selector) => (selector === '.close-x' ? closeButton : scroll),
  };
  const elements = {
    svReviewModal: modal,
    svReviewModalTitle: { textContent: '' },
    svReviewModalSubtitle: { textContent: '' },
    svReviewSummary: { innerHTML: '' },
    svReviewRecords: { innerHTML: '' },
    svReviewTimeline: { innerHTML: '' },
    svReviewActions: { innerHTML: '' },
  };
  const trigger = { focused: false, focus() { this.focused = true; } };
  const app = {
    $: (id) => elements[id] || null,
    esc: escapeHtml,
    getState: (key) => state.get(key),
    setState: (key, value) => state.set(key, value),
  };
  const document = {
    activeElement: trigger,
    addEventListener: () => {},
    contains: (element) => element === trigger,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const window = {
    AlarmApp: app,
    AlarmAudit: { mergeEvents: () => mergedEvents },
    addEventListener: () => {},
    location: { hash: '' },
    history: { replaceState: () => {} },
  };
  const context = {
    Blob,
    URL,
    document,
    window,
  };
  context.globalThis = context;
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/pages/supervisor.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'supervisor.js' });
  return { app, closeButton, context, elements, modal, scroll, state, trigger, window };
}

test('supervisor review duration handles minutes, hours, and invalid ranges', () => {
  const { context } = loadSupervisorPage();

  assert.equal(context.supervisorReviewDuration('2026-08-14T08:00:00Z', '2026-08-14T08:42:00Z'), '42 分鐘');
  assert.equal(context.supervisorReviewDuration('2026-08-14T08:00:00Z', '2026-08-14T10:05:00Z'), '2 小時 5 分鐘');
  assert.equal(context.supervisorReviewDuration('invalid', '2026-08-14T10:05:00Z'), '-');
  assert.equal(context.supervisorReviewDuration('2026-08-14T10:05:00Z', '2026-08-14T08:00:00Z'), '-');
});

test('supervisor review chooses the in-progress event and escapes field values', () => {
  const { app, context } = loadSupervisorPage();
  const order = {
    created_at: '2026-08-14T07:00:00Z',
    work_order_history: [
      { to_status: 'assigned', created_at: '2026-08-14T07:30:00Z' },
      { to_status: 'in_progress', created_at: '2026-08-14T08:00:00Z' },
    ],
  };

  assert.equal(context.supervisorReviewStartTime(order), '2026-08-14T08:00:00Z');
  const html = context.supervisorReviewField(app, '問題', '<img src=x onerror=alert(1)>', true);
  assert.match(html, /sv-review-field wide/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
});

test('supervisor review timeline renders newest events first and escapes audit content', () => {
  const mergedEvents = [
    { action: 'created', user_id: 'old-user', created_at: '2026-08-14T08:00:00Z' },
    {
      action: 'updated',
      user_id: '<new-user>',
      created_at: '2026-08-14T09:00:00Z',
      changes: [{ field: 'notes', from: 'safe', to: '<script>alert(1)</script>' }],
    },
  ];
  const { app, context, elements } = loadSupervisorPage({ mergedEvents });

  context.renderSupervisorReviewTimeline(app, { work_order_history: [] }, { issue_history: [] });

  const html = elements.svReviewTimeline.innerHTML;
  assert.ok(html.indexOf('&lt;new-user&gt;') < html.indexOf('old-user'));
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test('supervisor review modal opens with escaped data and restores focus when closed', () => {
  const orders = [{
    id: 'WO-1',
    issue_id: 'ISS-1',
    status: 'completed',
    manual: '808d',
    alarm_code: '3000',
    machine_id: '<CNC-1>',
    priority: 'high',
    created_at: '2026-08-14T07:00:00Z',
    completed_at: '2026-08-14T09:00:00Z',
    description: '<unsafe symptom>',
    work_order_history: [{ to_status: 'in_progress', created_at: '2026-08-14T08:00:00Z' }],
  }];
  const issues = [{ issue_id: 'ISS-1', line_id: 'LINE-A', issue_history: [] }];
  const page = loadSupervisorPage({ orders, issues });

  page.window.openSupervisorReviewModal('WO-1');

  assert.equal(page.modal.classList.contains('show'), true);
  assert.equal(page.scroll.scrollTop, 0);
  assert.equal(page.closeButton.focused, true);
  assert.equal(page.state.get('supervisorCurrentReviewOrderId'), 'WO-1');
  assert.match(page.elements.svReviewSummary.innerHTML, /&lt;CNC-1&gt;/);
  assert.match(page.elements.svReviewRecords.innerHTML, /&lt;unsafe symptom&gt;/);
  assert.doesNotMatch(page.elements.svReviewRecords.innerHTML, /<unsafe symptom>/);

  page.window.closeSupervisorReviewModal();

  assert.equal(page.modal.classList.contains('show'), false);
  assert.equal(page.state.get('supervisorCurrentReviewOrderId'), null);
  assert.equal(page.trigger.focused, true);
});
