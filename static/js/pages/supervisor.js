function supervisorApp() {
  return window.AlarmApp || null;
}

const VERIFIED_ORDER_STATUSES = new Set(['verified']);
const SUPERVISOR_RISK_STATUSES = new Set(['pending', 'assigned', 'in_progress']);
const SUPERVISOR_PRIORITIES = ['critical', 'high', 'medium', 'low'];
const SUPERVISOR_EDITABLE_STATUSES = ['pending', 'assigned', 'in_progress'];
const SUPERVISOR_PRIORITY_SCORE = { critical: 0, high: 1, medium: 2, low: 3 };

const SUPERVISOR_STATUS_LABELS = {
  open: '未處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '待驗證',
  verified: '已驗證',
  cancelled: '已取消',
  pending: '待處理',
};

function supervisorLabel(value, labels = SUPERVISOR_STATUS_LABELS) {
  return labels[value] || value || '-';
}

function supervisorCsvCell(value) {
  const text = String(value ?? '').replace(/"/g, '""');
  return `"${text}"`;
}

function downloadSupervisorCsv(filename, rows) {
  if (!rows.length) {
    setSupervisorResult('No data to export', true);
    return;
  }
  const csv = rows.map((row) => row.map(supervisorCsvCell).join(',')).join('\n');
  const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function supervisorJsArg(value) {
  return supervisorAttr(JSON.stringify(String(value ?? '')));
}

function supervisorAttr(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function supervisorEmpty(message) {
  return `<div class="wo-empty">${message}</div>`;
}

function supervisorTime(value) {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString('zh-TW', { hour12: false });
}

function supervisorAgeHours(value) {
  const date = new Date(value || '');
  if (Number.isNaN(date.getTime())) {
    return 0;
  }
  return Math.max(0, Math.round((Date.now() - date.getTime()) / 36_000) / 10);
}

function isSupervisorOrderOverdue(order) {
  return SUPERVISOR_RISK_STATUSES.has(order.status) && supervisorAgeHours(order.created_at) > 24;
}

function isSupervisorRiskOrder(order) {
  const priority = String(order.priority || '').toLowerCase();
  return (
    isSupervisorOrderOverdue(order) ||
    (SUPERVISOR_RISK_STATUSES.has(order.status) && !(order.assigned_to || '').trim()) ||
    (SUPERVISOR_RISK_STATUSES.has(order.status) && ['critical', 'high'].includes(priority))
  );
}

function setSupervisorResult(message, isError = false) {
  const app = supervisorApp();
  const result = app?.$('svResult');
  if (!app || !result) {
    return;
  }
  app.setResultMessage(result, `upload-result show${isError ? ' error' : ''}`, message);
}

function activeSupervisorData() {
  const app = supervisorApp();
  return {
    issues: app?.getState('supervisorIssues') || [],
    orders: app?.getState('supervisorOrders') || [],
  };
}

function buildSupervisorIssueMap(issues) {
  return issues.reduce((map, issue) => {
    map.set(issue.issue_id, issue);
    return map;
  }, new Map());
}

function supervisorMaintenanceUsers() {
  const app = supervisorApp();
  const users = app?.getState('supervisorUsers') || [];
  return users.filter((user) => user.active !== false && user.role === 'maintenance');
}

function supervisorOption(app, value, label, selectedValue) {
  const selected = value === selectedValue ? 'selected' : '';
  return `<option value="${supervisorAttr(value)}" ${selected}>${app.esc(label)}</option>`;
}

function supervisorAssigneeOptions(selectedUserId) {
  const app = supervisorApp();
  const maintenanceUsers = supervisorMaintenanceUsers();
  return [
    `<option value="" ${selectedUserId ? '' : 'selected'}>Unassigned</option>`,
    ...maintenanceUsers.map((user) =>
      `<option value="${supervisorAttr(user.user_id)}" ${user.user_id === selectedUserId ? 'selected' : ''}>${app.esc(user.name || user.user_id)}</option>`,
    ),
  ].join('');
}

function supervisorPriorityOptions(selectedPriority) {
  const app = supervisorApp();
  if (!app) {
    return '';
  }
  return SUPERVISOR_PRIORITIES
    .map((priority) => supervisorOption(app, priority, priority, selectedPriority))
    .join('');
}

function supervisorStatusOptions(selectedStatus) {
  const app = supervisorApp();
  if (!app) {
    return '';
  }
  return SUPERVISOR_EDITABLE_STATUSES
    .map((status) => supervisorOption(app, status, supervisorLabel(status), selectedStatus))
    .join('');
}

function renderSupervisorKpis(issueStats, orderStats, feedbackStats) {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  app.$('svOpenIssues').textContent = String(issueStats?.unresolved ?? 0);
  app.$('svPendingVerification').textContent = String(orderStats?.pending_verification ?? 0);
  app.$('svOverdue').textContent = String(orderStats?.overdue_open ?? 0);
  app.$('svCompletionRate').textContent = `${orderStats?.completion_rate ?? 0}%`;
  app.$('svFeedbackRate').textContent = feedbackStats?.rate || '0%';
}

function renderSupervisorLineOptions(issues) {
  const app = supervisorApp();
  const filter = app?.$('svLineFilter');
  if (!app || !filter) {
    return;
  }
  const current = filter.value;
  const lines = [...new Set(issues.map((issue) => issue.line_id || 'Unspecified'))].sort();
  filter.innerHTML = [
    '<option value="">全部產線</option>',
    ...lines.map((line) => `<option value="${supervisorAttr(line)}">${app.esc(line)}</option>`),
  ].join('');
  filter.value = lines.includes(current) ? current : '';
}

function renderSupervisorBulkAssigneeOptions() {
  const app = supervisorApp();
  const target = app?.$('svBulkAssignee');
  if (!app || !target) {
    return;
  }
  const maintenanceUsers = supervisorMaintenanceUsers();
  target.innerHTML = [
    '<option value="">Keep assignee</option>',
    '<option value="__unassigned__">Unassigned</option>',
    ...maintenanceUsers.map((user) =>
      `<option value="${supervisorAttr(user.user_id)}">${app.esc(user.name || user.user_id)}</option>`,
    ),
  ].join('');
}

function renderSupervisorVerificationOrder(app, order, issue) {
  const issueButton = order.issue_id
    ? `<button class="wo-btn alt" type="button" onclick="requestSupervisorRework(${supervisorJsArg(order.issue_id)})">退回重工</button>`
    : '';
  return `<div class="role-row">
    <div>
      <div class="wo-code">#${app.esc(order.id)} | ${app.esc(order.alarm_code || 'SYMPTOM')}</div>
      <div class="wo-desc">${app.esc(order.resolution || order.description || '')}</div>
      <div class="wo-meta">
        <span class="wo-badge">Issue ${app.esc(order.issue_id || '-')}</span>
        <span class="wo-badge">產線 ${app.esc(issue?.line_id || '-')}</span>
        <span class="wo-badge">機台 ${app.esc(order.machine_id || '-')}</span>
        <span class="wo-badge">完成者 ${app.esc(order.completed_by || order.updated_by || '-')}</span>
      </div>
    </div>
    <div class="role-row-actions">
      <button class="wo-btn" type="button" onclick="verifySupervisorOrder(${supervisorJsArg(order.id)})">驗證完成</button>
      ${issueButton}
    </div>
  </div>`;
}

function renderSupervisorVerificationQueue() {
  const app = supervisorApp();
  const queue = app?.$('svVerificationQueue');
  if (!app || !queue) {
    return;
  }
  const { issues, orders } = activeSupervisorData();
  const issueById = buildSupervisorIssueMap(issues);
  const completed = orders.filter((order) => order.status === 'completed');
  if (!completed.length) {
    queue.innerHTML = supervisorEmpty('目前沒有待驗證工單');
    return;
  }
  queue.innerHTML = completed
    .map((order) => renderSupervisorVerificationOrder(app, order, issueById.get(order.issue_id) || null))
    .join('');
}

function supervisorOrderMatchesText(order, issue, textFilter) {
  if (!textFilter) {
    return true;
  }
  return [order.id, order.issue_id, order.machine_id, order.assigned_to, order.updated_by, issue.description]
    .some((value) => String(value || '').toLowerCase().includes(textFilter));
}

function readSupervisorFilters(app) {
  return {
    line: app.$('svLineFilter')?.value || '',
    status: app.$('svStatusFilter')?.value || '',
    priority: app.$('svPriorityFilter')?.value || '',
    riskOnly: Boolean(app.$('svRiskOnly')?.checked),
    text: String(app.$('svTextFilter')?.value || '').trim().toLowerCase(),
  };
}

function supervisorOrderMatchesFilters(order, issue, filters) {
  const line = issue.line_id || '';
  if (filters.line && line !== filters.line) {
    return false;
  }
  if (filters.status && order.status !== filters.status) {
    return false;
  }
  if (filters.priority && order.priority !== filters.priority) {
    return false;
  }
  if (filters.riskOnly && !isSupervisorRiskOrder(order)) {
    return false;
  }
  return supervisorOrderMatchesText(order, issue, filters.text);
}

function visibleSupervisorOrders(app, issues, orders) {
  const filters = readSupervisorFilters(app);
  const issueById = buildSupervisorIssueMap(issues);
  const visibleOrders = orders.filter((order) => {
    if (VERIFIED_ORDER_STATUSES.has(order.status)) {
      return false;
    }
    return supervisorOrderMatchesFilters(order, issueById.get(order.issue_id) || {}, filters);
  });
  return { issueById, visibleOrders };
}

function renderSupervisorResponsibilityOrder(app, order, issue) {
  const editable = order.status !== 'completed';
  const editControls = editable ? `<div class="sv-order-edit">
    <select class="wo-select" id="svAssignee_${supervisorAttr(order.id)}" aria-label="Assignee">${supervisorAssigneeOptions(order.assigned_to || '')}</select>
    <select class="wo-select" id="svPriority_${supervisorAttr(order.id)}" aria-label="Priority">${supervisorPriorityOptions(order.priority || 'medium')}</select>
    <select class="wo-select" id="svStatus_${supervisorAttr(order.id)}" aria-label="Status">${supervisorStatusOptions(order.status || 'pending')}</select>
    <button class="wo-btn alt" type="button" onclick="updateSupervisorOrder(${supervisorJsArg(order.id)})">Update</button>
  </div>` : '';
  return `<div class="role-row compact">
    <div>
      <div class="wo-code">#${app.esc(order.id)} | ${app.esc(supervisorLabel(order.status))}</div>
      <div class="wo-desc">${app.esc(order.description || issue.description || '')}</div>
      <div class="wo-meta">
        <span class="wo-badge">負責 ${app.esc(order.assigned_to || '未指派')}</span>
        <span class="wo-badge">更新 ${app.esc(order.updated_by || '-')}</span>
        <span class="wo-badge">${app.esc(supervisorTime(order.updated_at))}</span>
        <span class="wo-badge">產線 ${app.esc(issue.line_id || '-')}</span>
      </div>
      ${editControls}
    </div>
  </div>`;
}

function renderSupervisorResponsibility() {
  const app = supervisorApp();
  const list = app?.$('svResponsibilityList');
  if (!app || !list) {
    return;
  }
  const { issues, orders } = activeSupervisorData();
  const { issueById, visibleOrders } = visibleSupervisorOrders(app, issues, orders);
  app.setState('supervisorVisibleOrders', visibleOrders);
  if (!visibleOrders.length) {
    list.innerHTML = supervisorEmpty('沒有符合條件的責任項目');
    return;
  }
  list.innerHTML = visibleOrders
    .map((order) => renderSupervisorResponsibilityOrder(app, order, issueById.get(order.issue_id) || {}))
    .join('');
}

function renderSupervisorRiskHotlist() {
  const app = supervisorApp();
  const target = app?.$('svRiskHotlist');
  if (!app || !target) {
    return;
  }
  const { issues, orders } = activeSupervisorData();
  const issueById = buildSupervisorIssueMap(issues);
  const riskOrders = orders
    .filter(isSupervisorRiskOrder)
    .sort((left, right) => {
      const leftScore = SUPERVISOR_PRIORITY_SCORE[left.priority] ?? 4;
      const rightScore = SUPERVISOR_PRIORITY_SCORE[right.priority] ?? 4;
      return leftScore - rightScore || supervisorAgeHours(right.created_at) - supervisorAgeHours(left.created_at);
    })
    .slice(0, 6);
  if (!riskOrders.length) {
    target.innerHTML = supervisorEmpty('No active risk items');
    return;
  }
  target.innerHTML = riskOrders.map((order) => {
    const issue = issueById.get(order.issue_id) || {};
    const reasons = [
      isSupervisorOrderOverdue(order) ? 'overdue' : '',
      !(order.assigned_to || '').trim() ? 'unassigned' : '',
      ['critical', 'high'].includes(String(order.priority || '').toLowerCase()) ? order.priority : '',
    ].filter(Boolean).join(' / ');
    return `<div class="sv-risk-card">
      <div class="risk-title">#${app.esc(order.id)} ${app.esc(order.alarm_code || 'SYMPTOM')}</div>
      <div class="risk-body">
        ${app.esc(reasons || 'risk')}<br>
        ${app.esc(order.machine_id || '-')} | ${app.esc(issue.line_id || '-')} | ${app.esc(String(supervisorAgeHours(order.created_at)))}h<br>
        ${app.esc(order.description || issue.description || '')}
      </div>
    </div>`;
  }).join('');
}

function exportSupervisorCsv() {
  const app = supervisorApp();
  const orders = app?.getState('supervisorVisibleOrders') || [];
  const issueById = buildSupervisorIssueMap(app?.getState('supervisorIssues') || []);
  downloadSupervisorCsv('supervisor-work-orders.csv', [
    ['id', 'issue_id', 'status', 'priority', 'machine_id', 'line_id', 'assigned_to', 'age_hours', 'description'],
    ...orders.map((order) => {
      const issue = issueById.get(order.issue_id) || {};
      return [
        order.id,
        order.issue_id,
        order.status,
        order.priority,
        order.machine_id,
        issue.line_id,
        order.assigned_to,
        supervisorAgeHours(order.created_at),
        order.description || issue.description || '',
      ];
    }),
  ]);
}

function supervisorWorkOrderPatch(app, values) {
  return {
    updated_by: app.currentUserId(),
    ...values,
  };
}

async function patchSupervisorOrder(app, orderId, values) {
  const data = await app.apiJson(`/work-orders/${encodeURIComponent(orderId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(supervisorWorkOrderPatch(app, values)),
  });
  if (data.status !== 'ok') {
    throw new Error(data.message || 'update failed');
  }
  return data;
}

async function updateSupervisorOrder(orderId) {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  const assignee = app.$(`svAssignee_${orderId}`)?.value || '';
  const priority = app.$(`svPriority_${orderId}`)?.value || 'medium';
  const status = app.$(`svStatus_${orderId}`)?.value || 'pending';
  try {
    await patchSupervisorOrder(app, orderId, { assigned_to: assignee, priority, status });
    setSupervisorResult(`Work order #${orderId} updated`);
    await loadSupervisorConsole();
  } catch (error) {
    setSupervisorResult(app.formatError(error, 'Work order update failed'), true);
  }
}

async function bulkUpdateSupervisorOrders() {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  const orders = (app.getState('supervisorVisibleOrders') || [])
    .filter((order) => order.status !== 'completed');
  if (!orders.length) {
    setSupervisorResult('No editable visible work orders', true);
    return;
  }
  const assigneeValue = app.$('svBulkAssignee')?.value || '';
  const priority = app.$('svBulkPriority')?.value || '';
  const status = app.$('svBulkStatus')?.value || '';
  if (!assigneeValue && !priority && !status) {
    setSupervisorResult('Choose at least one bulk update value', true);
    return;
  }
  if (!window.confirm(`Update ${orders.length} visible work order(s)?`)) {
    return;
  }
  const assignedTo = assigneeValue === '__unassigned__' ? '' : assigneeValue;
  let updated = 0;
  const failedIds = [];
  for (const order of orders) {
    try {
      const values = {
        ...(assigneeValue ? { assigned_to: assignedTo } : {}),
        ...(priority ? { priority } : {}),
        ...(status ? { status } : {}),
      };
      await patchSupervisorOrder(app, order.id, values);
      updated += 1;
    } catch (_) {
      failedIds.push(order.id);
    }
  }
  const failedText = failedIds.length ? `; failed: ${failedIds.join(', ')}` : '';
  setSupervisorResult(`Bulk update complete: ${updated} updated${failedText}`, failedIds.length > 0);
  await loadSupervisorConsole();
}

function renderSupervisorLineOverview(issueStats, orderStats) {
  const app = supervisorApp();
  const target = app?.$('svLineOverview');
  if (!app || !target) {
    return;
  }
  const lineEntries = Object.entries(issueStats?.by_line || {}).sort((left, right) => left[0].localeCompare(right[0]));
  const machineEntries = orderStats?.top_machines || [];
  target.innerHTML = `
    <div class="role-mini-grid">
      ${lineEntries.length ? lineEntries.map(([line, count]) => `<div class="role-mini-card"><span>${app.esc(line)}</span><b>${count}</b><small>issues</small></div>`).join('') : supervisorEmpty('沒有產線 Issue 統計')}
    </div>
    <div class="role-subtitle">Top Machines</div>
    <div class="role-mini-grid">
      ${machineEntries.length ? machineEntries.map((item) => `<div class="role-mini-card"><span>${app.esc(item.machine_id)}</span><b>${app.esc(String(item.count))}</b><small>work orders</small></div>`).join('') : supervisorEmpty('沒有機台統計')}
    </div>`;
}

function renderSupervisorAudit() {
  const app = supervisorApp();
  const target = app?.$('svAuditReview');
  if (!app || !target) {
    return;
  }
  const { issues, orders } = activeSupervisorData();
  const events = [
    ...issues.flatMap((issue) => (Array.isArray(issue.issue_history) ? issue.issue_history.map((event) => ({ ...event, source_id: issue.issue_id, audit_source: 'issue' })) : [])),
    ...orders.flatMap((order) => (Array.isArray(order.work_order_history) ? order.work_order_history.map((event) => ({ ...event, source_id: order.id, audit_source: 'work order' })) : [])),
  ].sort((left, right) => new Date(right.created_at || 0) - new Date(left.created_at || 0)).slice(0, 12);
  if (!events.length) {
    target.innerHTML = supervisorEmpty('尚無 audit events');
    return;
  }
  target.innerHTML = events.map((event) => {
    const statusText = event.from_status || event.to_status ? `${event.from_status || '-'} -> ${event.to_status || '-'}` : '';
    const changes = window.AlarmAudit?.renderChanges(app, event) || '';
    return `<div class="audit-event">
      <div class="audit-dot"></div>
      <div class="audit-body">
        <div class="audit-title">${app.esc(event.audit_source)} ${app.esc(event.source_id)} ${statusText ? `<span>${app.esc(statusText)}</span>` : ''}</div>
        <div class="audit-meta">${app.esc(event.action || 'updated')} by ${app.esc(event.user_id || 'system')} | ${app.esc(supervisorTime(event.created_at))}</div>
        ${changes}
      </div>
    </div>`;
  }).join('');
}

async function loadSupervisorConsole() {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  try {
    const [issuesData, ordersData, issueStats, orderStats, feedbackStats, usersData] = await Promise.all([
      app.apiJson('/issues'),
      app.apiJson('/work-orders'),
      app.apiJson('/issues/stats'),
      app.apiJson('/work-orders/stats'),
      app.apiJson('/feedback/stats'),
      app.apiJson('/mock-users').catch(() => ({ users: [] })),
    ]);
    app.patchState({
      supervisorIssues: issuesData.issues || [],
      supervisorOrders: ordersData.orders || [],
      supervisorUsers: usersData.users || [],
    });
    renderSupervisorKpis(issueStats, orderStats, feedbackStats);
    renderSupervisorLineOptions(issuesData.issues || []);
    renderSupervisorBulkAssigneeOptions();
    renderSupervisorVerificationQueue();
    renderSupervisorRiskHotlist();
    renderSupervisorResponsibility();
    renderSupervisorLineOverview(issueStats, orderStats);
    renderSupervisorAudit();
  } catch (error) {
    setSupervisorResult(app.formatError(error, 'Supervisor console 載入失敗'), true);
  }
}

async function verifySupervisorOrder(orderId) {
  const app = supervisorApp();
  const order = (app?.getState('supervisorOrders') || []).find((item) => item.id === orderId);
  if (!app || !order) {
    return;
  }
  try {
    const userId = app.currentUserId();
    await app.apiJson(`/work-orders/${encodeURIComponent(orderId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'verified', verified_by: userId, updated_by: userId }),
    });
    if (order.issue_id) {
      await app.apiJson(`/issues/${encodeURIComponent(order.issue_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'verified', updated_by: userId }),
      });
    }
    setSupervisorResult(`工單 #${orderId} 已驗證`);
    await loadSupervisorConsole();
  } catch (error) {
    setSupervisorResult(app.formatError(error, '驗證失敗'), true);
  }
}

async function requestSupervisorRework(issueId) {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  const note = window.prompt('請輸入退回重工原因');
  if (!note) {
    return;
  }
  try {
    await app.apiJson(`/issues/${encodeURIComponent(issueId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'open', operator_note: `[Supervisor rework] ${note}` }),
    });
    setSupervisorResult(`Issue ${issueId} 已退回重工`);
    await loadSupervisorConsole();
  } catch (error) {
    setSupervisorResult(app.formatError(error, '退回重工失敗'), true);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = supervisorApp();
  if (!app) {
    return;
  }
  app.initCommonPageBindings();
  app.$('supervisorUserLabel').textContent = `${app.currentUserId()} (${app.currentUserRole()})`;
  ['svLineFilter', 'svStatusFilter', 'svPriorityFilter', 'svTextFilter', 'svRiskOnly'].forEach((id) => {
    app.$(id)?.addEventListener('input', renderSupervisorResponsibility);
    app.$(id)?.addEventListener('change', renderSupervisorResponsibility);
  });
  loadSupervisorConsole();
});

window.loadSupervisorConsole = loadSupervisorConsole;
window.verifySupervisorOrder = verifySupervisorOrder;
window.requestSupervisorRework = requestSupervisorRework;
window.exportSupervisorCsv = exportSupervisorCsv;
window.updateSupervisorOrder = updateSupervisorOrder;
window.bulkUpdateSupervisorOrders = bulkUpdateSupervisorOrders;
