const MAINTENANCE_COLUMNS = [
  { key: 'pending', title: '待處理' },
  { key: 'assigned', title: '已指派' },
  { key: 'in_progress', title: '處理中' },
  { key: 'completed', title: '已完成' },
  { key: 'verified', title: '已驗證' },
];

const MAINTENANCE_STATUS_LABELS = {
  pending: '待處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '已完成',
  verified: '已驗證',
};

const MAINTENANCE_PRIORITY_LABELS = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '緊急',
};
const MAINTENANCE_KB_REVIEW_LABELS = {
  not_ready: '尚未成為候選知識',
  pending_review: '等待 Admin 審核',
  needs_revision: 'Admin 退回補充',
  rejected: '不採用',
  ingested: '已寫入知識庫',
  validation_failed: '寫入失敗，等待 Admin 處理',
};

function maintenanceApp() {
  return window.AlarmApp || null;
}

function currentMaintenanceUser() {
  return maintenanceApp()?.currentUserId?.() || 'maintenance01';
}

function normalizeFilterText(value) {
  return String(value || '').trim().toLowerCase();
}

function includesFilter(values, filterText) {
  if (!filterText) return true;
  return values.some((value) => normalizeFilterText(value).includes(filterText));
}

function formatMaintenanceTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-TW', { hour12: false });
}

function issueStatusLabel(status) {
  return {
    open: '未處理',
    assigned: '已指派',
    in_progress: '處理中',
    completed: '已完成',
    verified: '已驗證',
    cancelled: '已取消',
  }[status] || status || '未處理';
}

function issueSeverityLabel(severity) {
  return {
    info: '資訊',
    low: '低',
    medium: '中',
    high: '高',
    critical: '緊急',
  }[severity] || severity || '中';
}

function currentIssueFilter() {
  return normalizeFilterText(document.getElementById('mtIssueFilter')?.value);
}

function currentWorkFilters() {
  return {
    view: document.getElementById('mtWorkView')?.value || 'active',
    priority: document.getElementById('mtPriorityFilter')?.value || '',
    text: normalizeFilterText(document.getElementById('mtWorkFilter')?.value),
  };
}

function filterMaintenanceIssues(issues) {
  const filterText = currentIssueFilter();
  return (issues || []).filter((issue) => includesFilter([
    issue.issue_id,
    issue.machine_id,
    issue.line_id,
    issue.alarm_code,
    issue.description,
    issue.source,
  ], filterText));
}

function filterWorkOrders(orders) {
  const filters = currentWorkFilters();
  return (orders || []).filter((order) => {
    if (filters.view === 'active' && order.status === 'verified') return false;
    if (filters.view === 'verified' && order.status !== 'verified') return false;
    if (filters.priority && order.priority !== filters.priority) return false;
    if (filters.view === 'unassigned' && (order.assigned_to || ['completed', 'verified'].includes(order.status))) return false;
    if (filters.view === 'mine' && order.assigned_to !== currentMaintenanceUser()) return false;
    return includesFilter([
      order.id,
      order.issue_id,
      order.machine_id,
      order.alarm_code,
      order.description,
      order.assigned_to,
      order.source,
    ], filters.text);
  });
}

function currentMaintenanceColumns() {
  const { view } = currentWorkFilters();
  if (view === 'verified') return MAINTENANCE_COLUMNS.filter((column) => column.key === 'verified');
  if (view === 'active' || view === 'unassigned') return MAINTENANCE_COLUMNS.filter((column) => column.key !== 'verified');
  return MAINTENANCE_COLUMNS;
}

function rerenderMaintenanceData() {
  const app = maintenanceApp();
  if (!app) return;
  renderMaintenanceIssues(filterMaintenanceIssues(app.getState('maintenanceOpenIssues') || []));
  renderWorkOrders(filterWorkOrders(app.getState('maintenanceOrders') || []));
}

function findCurrentIssue(issueId) {
  return (maintenanceApp()?.getState('maintenanceIssues') || []).find((issue) => issue.issue_id === issueId);
}

function findCurrentOrder(orderId) {
  return (maintenanceApp()?.getState('maintenanceOrders') || []).find((order) => order.id === orderId);
}

function getMaintenanceOrder(orderId) {
  return findCurrentOrder(orderId);
}

function workOrderCard(order) {
  const app = maintenanceApp();
  const description = order.description ? order.description.slice(0, 90) : '無描述';
  const quickAction = {
    pending: `<button class="wo-btn alt" onclick="event.stopPropagation(); acceptWorkOrder(${app.toJsArg(order.id)})">接手</button>`,
    assigned: `<button class="wo-btn alt" onclick="event.stopPropagation(); startWorkOrder(${app.toJsArg(order.id)})">開始</button>`,
    in_progress: `<button class="wo-btn alt" onclick="event.stopPropagation(); completeWorkOrder(${app.toJsArg(order.id)})">完成</button>`,
  }[order.status] || '';

  return `<div class="wo-card" onclick="openMaintenanceModal(${app.toJsArg(order.id)})">
    <div class="wo-code">#${app.esc(order.id)} | ${app.esc(order.alarm_code || 'SYMPTOM')}</div>
    <div class="wo-desc">${app.esc(description)}</div>
    <div class="wo-meta">
      <span class="wo-badge prio-${app.esc(order.priority || 'medium')}">優先 ${app.esc(MAINTENANCE_PRIORITY_LABELS[order.priority] || order.priority || '中')}</span>
      <span class="wo-badge status-pill-sm">${app.esc(MAINTENANCE_STATUS_LABELS[order.status] || order.status || '待處理')}</span>
      <span class="wo-badge">機台 ${app.esc(order.machine_id || 'N/A')}</span>
      ${order.issue_id ? `<span class="wo-badge">問題 ${app.esc(order.issue_id)}</span>` : ''}
      ${order.kb_review_status && order.kb_review_status !== 'not_ready'
        ? `<span class="wo-badge">${app.esc(MAINTENANCE_KB_REVIEW_LABELS[order.kb_review_status] || order.kb_review_status)}</span>`
        : ''}
      ${quickAction}
    </div>
  </div>`;
}

function archiveOrderCard(order) {
  const app = maintenanceApp();
  const description = order.description ? order.description.slice(0, 120) : '無描述';
  return `<div class="archive-order-card">
    <div class="archive-order-main">
      <div class="wo-code">#${app.esc(order.id)} | ${app.esc(order.alarm_code || 'SYMPTOM')}</div>
      <div class="wo-desc">${app.esc(description)}</div>
      <div class="wo-meta">
        <span class="wo-badge status-pill-sm">${app.esc(MAINTENANCE_STATUS_LABELS[order.status] || order.status || '已完成')}</span>
        <span class="wo-badge">機台 ${app.esc(order.machine_id || 'N/A')}</span>
        ${order.issue_id ? `<span class="wo-badge">問題 ${app.esc(order.issue_id)}</span>` : ''}
        <span class="wo-badge">完成 ${app.esc(formatMaintenanceTime(order.completed_at || order.updated_at))}</span>
        <span class="wo-badge">封存 ${app.esc(order.archive_file || '-')}</span>
      </div>
    </div>
  </div>`;
}

function renderArchiveOrders() {
  const app = maintenanceApp();
  const board = app?.$('maintenanceWorkBoard');
  if (!app || !board) return;

  const archives = app.getState('maintenanceArchiveFiles') || [];
  const archivedOrders = app.getState('maintenanceArchivedOrders') || [];
  delete board.dataset.columnCount;

  if (!archivedOrders.length) {
    board.innerHTML = `<div class="maintenance-empty-state">
      <div class="maintenance-empty-title">目前沒有封存工單</div>
      <div class="maintenance-empty-text">已驗證或已完成的舊工單封存後，會顯示在這裡。</div>
    </div>`;
    return;
  }

  const latestArchive = archives[0];
  board.innerHTML = `<div class="maintenance-archive-view">
    <div class="archive-summary">
      <div>
        <div class="archive-kicker">Archive summary</div>
        <div class="archive-title">${archivedOrders.length} 筆封存工單</div>
        <div class="archive-sub">來源：alarm_db/archive${latestArchive ? `，最近檔案 ${app.esc(latestArchive.file)}` : ''}</div>
      </div>
      <div class="archive-file-list">
        ${archives.map((archive) => `<span class="wo-badge">${app.esc(archive.file)} · ${archive.count}</span>`).join('')}
      </div>
    </div>
    <div class="archive-order-list">${archivedOrders.map(archiveOrderCard).join('')}</div>
  </div>`;
}

function renderWorkOrders(orders) {
  const app = maintenanceApp();
  const board = app?.$('maintenanceWorkBoard');
  if (!app || !board) return;

  if (currentWorkFilters().view === 'verified') {
    renderArchiveOrders();
    return;
  }

  const columns = currentMaintenanceColumns();
  board.dataset.columnCount = String(columns.length);
  board.innerHTML = columns.map((column) => {
    const items = orders.filter((order) => order.status === column.key);
    const cards = items.length ? items.map(workOrderCard).join('') : '<div class="maintenance-column-empty">無工單</div>';
    return `<div class="wo-col">
      <div class="wo-col-head">
        <span class="wo-col-title">${column.title}</span>
        <span class="wo-col-count">${items.length}</span>
      </div>
      ${cards}
    </div>`;
  }).join('');
}

function renderMaintenanceIssues(issues) {
  const app = maintenanceApp();
  const list = app?.$('maintenanceIssueList');
  if (!app || !list) return;

  if (!issues.length) {
    list.innerHTML = '<div class="wo-empty">目前沒有待維修問題</div>';
    return;
  }

  list.innerHTML = issues.map((issue) => {
    const action = issue.work_order_id
      ? `<span class="wo-badge">工單 #${app.esc(issue.work_order_id)}</span>`
      : `<button class="wo-btn alt" onclick="createWorkOrderFromIssue(${app.toJsArg(issue.issue_id)})">建立工單</button>`;
    return `<div class="wo-card" style="cursor:default">
      <div class="wo-code">${app.esc(issue.issue_id)} ${issue.alarm_code ? `| Alarm ${app.esc(issue.alarm_code)}` : ''}</div>
      <div class="wo-desc">${app.esc(issue.description || '')}</div>
      <div class="wo-meta">
        <span class="wo-badge">機台 ${app.esc(issue.machine_id || 'N/A')}</span>
        <span class="wo-badge">產線 ${app.esc(issue.line_id || 'N/A')}</span>
        <span class="wo-badge">狀態 ${app.esc(issueStatusLabel(issue.status))}</span>
        <span class="wo-badge">嚴重度 ${app.esc(issueSeverityLabel(issue.severity))}</span>
        <span class="wo-badge">來源 ${app.esc(issue.source || 'operator')}</span>
        ${action}
      </div>
    </div>`;
  }).join('');
}

function updateMaintenanceStats(issues, orders, feedbackStats) {
  const app = maintenanceApp();
  if (!app) return;

  app.$('mtIssueOpen').textContent = String(issues.length);
  app.$('mtUnassigned').textContent = String(orders.filter((order) => !order.assigned_to && !['completed', 'verified'].includes(order.status)).length);
  app.$('mtInProgress').textContent = String(orders.filter((order) => ['assigned', 'in_progress'].includes(order.status)).length);
  app.$('mtFeedbackCount').textContent = String(feedbackStats?.technician_feedback ?? 0);
}

async function loadMaintenanceData() {
  const app = maintenanceApp();
  if (!app) return;

  try {
    const [issuesData, ordersData, archiveData, feedbackStats] = await Promise.all([
      app.apiPaged('/issues/page', 'issues'),
      app.apiPaged('/work-orders/page', 'orders'),
      app.apiJson('/work-orders/archive').catch(() => ({ orders: [], archives: [] })),
      app.apiJson('/feedback/stats').catch(() => ({ technician_feedback: 0 })),
    ]);
    const issues = issuesData.issues || [];
    const openIssues = issues.filter((issue) => !['completed', 'verified', 'cancelled'].includes(issue.status));
    const orders = ordersData.orders || [];
    app.patchState({
      maintenanceIssues: issues,
      maintenanceOpenIssues: openIssues,
      maintenanceOrders: orders,
      maintenanceArchivedOrders: archiveData.orders || [],
      maintenanceArchiveFiles: archiveData.archives || [],
    });
    renderMaintenanceIssues(filterMaintenanceIssues(openIssues));
    renderWorkOrders(filterWorkOrders(orders));
    renderMaintenanceResolvedCalendar();
    updateMaintenanceStats(openIssues, orders, feedbackStats);
  } catch (error) {
    const list = app.$('maintenanceIssueList');
    if (list) {
      list.innerHTML = `<div class="wo-empty">${app.esc(app.formatError(error, '載入維修資料失敗'))}</div>`;
    }
  }
}

async function createWorkOrderFromIssue(issueId) {
  const app = maintenanceApp();
  if (!app) return;

  try {
    const data = await app.apiJson(`/issues/${encodeURIComponent(issueId)}/escalate`, { method: 'POST' });
    if (data.status !== 'ok') throw new Error(data.message || '建立工單失敗');
    await loadMaintenanceData();
  } catch (error) {
    window.alert(app.formatError(error, '建立工單失敗'));
  }
}

async function patchMaintenanceWorkOrder(orderId, payload, fallbackMessage) {
  const app = maintenanceApp();
  if (!app) return null;
  const order = getMaintenanceOrder(orderId);
  const body = {
    ...payload,
    updated_by: payload.updated_by || currentMaintenanceUser(),
    ...(payload.version !== undefined || order?.version === undefined ? {} : { version: order.version }),
  };

  try {
    const data = await app.apiJson(`/work-orders/${encodeURIComponent(orderId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (data.status !== 'ok') throw new Error(data.message || fallbackMessage);
    await loadMaintenanceData();
    return data.order;
  } catch (error) {
    window.alert(app.formatError(error, fallbackMessage));
    return null;
  }
}

async function acceptWorkOrder(orderId) {
  await patchMaintenanceWorkOrder(orderId, {
    status: 'in_progress',
    assigned_to: currentMaintenanceUser(),
    accepted_by: currentMaintenanceUser(),
  }, '接手工單失敗');
}

async function startWorkOrder(orderId) {
  await patchMaintenanceWorkOrder(orderId, {
    status: 'in_progress',
    assigned_to: currentMaintenanceUser(),
    accepted_by: currentMaintenanceUser(),
  }, '開始工單失敗');
}

async function completeWorkOrder(orderId) {
  const app = maintenanceApp();
  const order = findCurrentOrder(orderId);
  if (!app || !order) return;
  openMaintenanceModal(orderId);
  app.$('mtEditStatus').value = 'completed';
  app.$('mtEditCompletedBy').value = app.$('mtEditCompletedBy').value.trim() || currentMaintenanceUser();
  app.$('mtEditResolution').focus();
}

function maintenanceResolvedItem(order) {
  return {
    id: order.id,
    title: `#${order.id} | ${order.alarm_code || 'SYMPTOM'}`,
    description: order.resolution || order.description || '',
    status: order.status,
    machine: order.machine_id || 'N/A',
    issueId: order.issue_id || '',
    date: order.completed_at || order.updated_at || order.created_at,
    archiveFile: order.archive_file || '',
  };
}

function renderMaintenanceResolvedItem(item) {
  const app = maintenanceApp();
  const action = item.archiveFile
    ? `<span class="wo-badge">封存 ${app.esc(item.archiveFile)}</span>`
    : `<button class="wo-btn alt" onclick="openMaintenanceModal(${app.toJsArg(item.id)})">查看</button>`;
  return `<div class="resolved-item">
    <div class="wo-code">${app.esc(item.title)}</div>
    <div class="wo-desc">${app.esc(item.description.slice(0, 140) || '無描述')}</div>
    <div class="wo-meta">
      <span class="wo-badge">${app.esc(MAINTENANCE_STATUS_LABELS[item.status] || item.status || '已完成')}</span>
      <span class="wo-badge">機台 ${app.esc(item.machine)}</span>
      ${item.issueId ? `<span class="wo-badge">問題 ${app.esc(item.issueId)}</span>` : ''}
      ${action}
    </div>
  </div>`;
}

function renderMaintenanceResolvedCalendar() {
  const app = maintenanceApp();
  if (!app?.renderResolvedCalendar) return;

  const orders = app.getState('maintenanceOrders') || [];
  const archivedOrders = app.getState('maintenanceArchivedOrders') || [];
  const items = [
    ...orders.filter((order) => ['completed', 'verified'].includes(order.status)),
    ...archivedOrders,
  ].map(maintenanceResolvedItem);
  const monthKey = app.getState('maintenanceResolvedMonth') || app.calendarMonthKey();
  const selectedDate = app.getState('maintenanceResolvedDate') || app.calendarDateKey(new Date().toISOString());
  app.renderResolvedCalendar({
    calendarEl: app.$('maintenanceResolvedCalendar'),
    listEl: app.$('maintenanceResolvedList'),
    items,
    monthKey,
    selectedDate,
    renderItem: renderMaintenanceResolvedItem,
    emptyText: '這天沒有已解決工單。',
  });
}

function bindMaintenanceResolvedCalendar() {
  const app = maintenanceApp();
  const calendar = app?.$('maintenanceResolvedCalendar');
  if (!app || !calendar || calendar.dataset.bound === 'true') return;
  calendar.dataset.bound = 'true';

  calendar.addEventListener('click', (event) => {
    const actionButton = event.target.closest('[data-calendar-action]');
    if (actionButton) {
      const offset = actionButton.dataset.calendarAction === 'prev' ? -1 : 1;
      app.setState('maintenanceResolvedMonth', app.shiftCalendarMonth(app.getState('maintenanceResolvedMonth'), offset));
      renderMaintenanceResolvedCalendar();
      return;
    }
    const dayButton = event.target.closest('[data-calendar-date]');
    if (dayButton) {
      app.setState('maintenanceResolvedDate', dayButton.dataset.calendarDate);
      renderMaintenanceResolvedCalendar();
    }
  });
}

function renderMaintenanceIssueContext(order) {
  const app = maintenanceApp();
  const box = app?.$('mtIssueContext');
  if (!app || !box) return;

  const issue = order.issue_id ? findCurrentIssue(order.issue_id) : null;
  if (!issue) {
    box.innerHTML = `<div class="maintenance-context-title">問題脈絡</div>
      <div class="maintenance-context-grid">
        <div><span>問題</span><b>${app.esc(order.issue_id || 'N/A')}</b></div>
        <div><span>來源</span><b>${app.esc(order.source || 'manual')}</b></div>
        <div><span>機台</span><b>${app.esc(order.machine_id || 'N/A')}</b></div>
        <div><span>Alarm</span><b>${app.esc(order.alarm_code || 'SYMPTOM')}</b></div>
      </div>`;
    return;
  }

  const notes = Array.isArray(issue.operator_notes) ? issue.operator_notes : [];
  const latestNote = notes.length ? notes[notes.length - 1].note : '';
  box.innerHTML = `<div class="maintenance-context-title">Operator 問題脈絡</div>
    <div class="maintenance-context-grid">
      <div><span>問題</span><b>${app.esc(issue.issue_id || 'N/A')}</b></div>
      <div><span>來源</span><b>${app.esc(issue.source || 'operator')}</b></div>
      <div><span>產線</span><b>${app.esc(issue.line_id || 'N/A')}</b></div>
      <div><span>狀態</span><b>${app.esc(issueStatusLabel(issue.status))}</b></div>
    </div>
    <div class="maintenance-context-text">${app.esc(issue.original_description || issue.description || '')}</div>
    ${latestNote ? `<div class="maintenance-context-note">${app.esc(latestNote)}</div>` : ''}`;
}

function renderMaintenanceTimeline(order) {
  const app = maintenanceApp();
  const box = app?.$('mtWorkTimeline');
  if (!app || !box) return;

  const issue = order.issue_id ? findCurrentIssue(order.issue_id) : null;
  const events = window.AlarmAudit?.mergeEvents(order.work_order_history, issue?.issue_history) || [];
  if (!events.length) {
    box.innerHTML = '';
    return;
  }

  box.innerHTML = `<div class="maintenance-context-title">Audit timeline</div>
    ${events.map((event) => {
      const statusText = event.from_status || event.to_status
        ? `${event.from_status || '-'} -> ${event.to_status || '-'}`
        : '';
      const changes = window.AlarmAudit?.renderChanges(app, event) || '';
      return `<div class="audit-event">
        <div class="audit-dot"></div>
        <div class="audit-body">
          <div class="audit-title">${app.esc(event.audit_source || 'audit')} · ${app.esc(event.action || 'updated')} ${statusText ? `<span>${app.esc(statusText)}</span>` : ''}</div>
          <div class="audit-meta">${app.esc(event.user_id || 'system')} · ${app.esc(formatMaintenanceTime(event.created_at))}</div>
          ${changes}
        </div>
      </div>`;
    }).join('')}`;
}

function resetMaintenanceModalScroll() {
  const app = maintenanceApp();
  const modal = app?.$('maintenanceModal');
  const card = modal?.querySelector('.wo-modal-card');
  if (!modal || !card) return;
  modal.scrollTop = 0;
  card.scrollTop = 0;
  modal.querySelectorAll('textarea').forEach((field) => {
    field.scrollTop = 0;
  });
}

function openMaintenanceModal(orderId) {
  const app = maintenanceApp();
  const order = findCurrentOrder(orderId);
  if (!app || !order) return;

  app.setState('maintenanceCurrentOrderId', orderId);
  app.$('mtModalTitle').textContent = `工單 #${order.id}`;
  app.$('mtModalId').textContent = `問題 ${order.issue_id || 'N/A'} | ${String(order.manual || '808d').toUpperCase()}`;
  app.$('mtEditStatus').value = order.status || 'pending';
  app.$('mtEditPriority').value = order.priority || 'medium';
  app.$('mtEditAssignee').value = order.assigned_to || currentMaintenanceUser();
  app.$('mtEditMachine').value = order.machine_id || '';
  app.$('mtEditDesc').value = order.description || '';
  app.$('mtEditRagSuggestion').value = order.rag_suggestion || '無參考建議';
  app.$('mtEditResolution').value = order.resolution || '';
  app.$('mtEditNotes').value = order.notes || '';
  app.$('mtEditAcceptedBy').value = order.accepted_by || '';
  app.$('mtEditCompletedBy').value = order.completed_by || '';
  app.$('mtEditRootCause').value = order.root_cause || '';
  app.$('mtEditFailureCategory').value = order.failure_category || '';
  app.$('mtEditLlmCorrectness').value = order.llm_correctness || '';
  app.$('mtEditLlmCoverage').value = order.llm_coverage || '';
  app.$('mtEditRepairAction').value = order.repair_action || '';
  app.$('mtEditLlmMissingInfo').value = order.llm_missing_info || '';
  const reviewBox = app.$('mtKnowledgeReview');
  const reviewStatus = order.kb_review_status || 'not_ready';
  if (reviewBox) {
    reviewBox.style.display = reviewStatus === 'not_ready' ? 'none' : 'block';
    reviewBox.textContent = `${MAINTENANCE_KB_REVIEW_LABELS[reviewStatus] || reviewStatus}` +
      `${order.kb_review_note ? `：${order.kb_review_note}` : ''}`;
  }
  renderMaintenanceIssueContext(order);
  renderMaintenanceTimeline(order);
  app.$('maintenanceModal').classList.add('show');
  resetMaintenanceModalScroll();
}

function closeMaintenanceModal() {
  const app = maintenanceApp();
  if (!app) return;
  app.$('maintenanceModal').classList.remove('show');
  app.setState('maintenanceCurrentOrderId', null);
}

async function saveMaintenanceWorkOrder() {
  const app = maintenanceApp();
  const orderId = app?.getState('maintenanceCurrentOrderId');
  if (!app || !orderId) return;
  const order = getMaintenanceOrder(orderId);

  const payload = {
    status: app.$('mtEditStatus').value,
    priority: app.$('mtEditPriority').value,
    assigned_to: app.$('mtEditAssignee').value.trim(),
    machine_id: app.$('mtEditMachine').value.trim(),
    description: app.$('mtEditDesc').value.trim(),
    resolution: app.$('mtEditResolution').value.trim(),
    notes: app.$('mtEditNotes').value.trim(),
    accepted_by: app.$('mtEditAcceptedBy').value.trim(),
    completed_by: app.$('mtEditCompletedBy').value.trim(),
    root_cause: app.$('mtEditRootCause').value.trim(),
    repair_action: app.$('mtEditRepairAction').value.trim(),
    failure_category: app.$('mtEditFailureCategory').value.trim(),
    llm_correctness: app.$('mtEditLlmCorrectness').value,
    llm_coverage: app.$('mtEditLlmCoverage').value,
    llm_missing_info: app.$('mtEditLlmMissingInfo').value.trim(),
    llm_expected_fix: app.$('mtEditResolution').value.trim(),
    llm_answer_used: Boolean(app.$('mtEditLlmCorrectness').value || app.$('mtEditLlmCoverage').value),
    updated_by: currentMaintenanceUser(),
    ...(order?.version === undefined ? {} : { version: order.version }),
  };

  if (payload.status === 'in_progress' && !payload.accepted_by) payload.accepted_by = currentMaintenanceUser();
  if (payload.status === 'completed' && !payload.completed_by) payload.completed_by = currentMaintenanceUser();
  if (payload.status === 'completed' && (!payload.root_cause || !payload.repair_action)) {
    window.alert('完成工單前，請填寫根本原因與實際維修動作。');
    (payload.root_cause ? app.$('mtEditRepairAction') : app.$('mtEditRootCause')).focus();
    return;
  }
  if (payload.status === 'verified') {
    window.alert('維修人員不能直接驗證工單，請由 Operator 或 Supervisor 確認完成。');
    return;
  }
  try {
    const data = await app.apiJson(`/work-orders/${encodeURIComponent(orderId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.status !== 'ok') throw new Error(data.message || '儲存工單失敗');
    closeMaintenanceModal();
    await loadMaintenanceData();
  } catch (error) {
    window.alert(app.formatError(error, '儲存工單失敗'));
  }
}

async function deleteMaintenanceWorkOrder() {
  const app = maintenanceApp();
  const orderId = app?.getState('maintenanceCurrentOrderId');
  if (!app || !orderId) return;
  if (!window.confirm('確定要刪除此工單嗎？')) return;

  try {
    await app.apiJson(`/work-orders/${encodeURIComponent(orderId)}`, { method: 'DELETE' });
    closeMaintenanceModal();
    await loadMaintenanceData();
  } catch (error) {
    window.alert(app.formatError(error, '刪除工單失敗'));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = maintenanceApp();
  if (!app) return;

  app.$('maintenanceUserLabel').textContent = `${app.currentUserId?.() || ''} (${app.currentUserRole?.() || ''})`;

  const url = new URL(window.location.href);
  const tab = url.searchParams.get('tab');
  const code = url.searchParams.get('code');
  const manual = url.searchParams.get('manual');
  const activeTab = ['resolved', 'lookup', 'chat'].includes(tab) ? tab : 'workbench';

  app.switchTab(activeTab, document.querySelector(`.tab-btn[data-tab="${activeTab}"]`));
  app.renderHistory?.();

  app.$('searchInput')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') doSearch();
  });

  if (manual) {
    const manualBtn = document.querySelector(`#panel-lookup .manual-btn[data-name="${manual}"]`);
    if (manualBtn) selectManual(manualBtn);
  }
  if (code) {
    app.switchTab('lookup', document.querySelector('.tab-btn[data-tab="lookup"]'));
    if (app.$('searchInput')) app.$('searchInput').value = code;
    doSearch(code, manual || app.getState('lookupManual'));
  }

  app.$('maintenanceModal')?.addEventListener('click', (event) => {
    if (event.target === app.$('maintenanceModal')) closeMaintenanceModal();
  });

  ['mtIssueFilter', 'mtWorkView', 'mtPriorityFilter', 'mtWorkFilter'].forEach((id) => {
    app.$(id)?.addEventListener('input', rerenderMaintenanceData);
    app.$(id)?.addEventListener('change', rerenderMaintenanceData);
  });

  bindMaintenanceResolvedCalendar();
  loadMaintenanceData();
});

window.loadMaintenanceData = loadMaintenanceData;
window.createWorkOrderFromIssue = createWorkOrderFromIssue;
window.acceptWorkOrder = acceptWorkOrder;
window.startWorkOrder = startWorkOrder;
window.completeWorkOrder = completeWorkOrder;
window.openMaintenanceModal = openMaintenanceModal;
window.closeMaintenanceModal = closeMaintenanceModal;
window.saveMaintenanceWorkOrder = saveMaintenanceWorkOrder;
window.deleteMaintenanceWorkOrder = deleteMaintenanceWorkOrder;
