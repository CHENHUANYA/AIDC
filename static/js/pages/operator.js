function operatorApp() {
  return window.AlarmApp || null;
}

const ISSUE_STATUS_LABELS = {
  open: '未處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '已完成',
  verified: '已驗證',
  cancelled: '已取消',
};

const ISSUE_SEVERITY_LABELS = {
  info: '資訊',
  low: '低',
  medium: '中',
  high: '高',
  critical: '緊急',
};

const WORK_ORDER_STATUS_LABELS = {
  pending: '待處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '已完成',
  verified: '已驗證',
};

function issueStatusLabel(status) {
  return ISSUE_STATUS_LABELS[status] || status || '未處理';
}

function issueSeverityLabel(severity) {
  return ISSUE_SEVERITY_LABELS[severity] || severity || '中';
}

function workOrderStatusLabel(status) {
  return WORK_ORDER_STATUS_LABELS[status] || status || '-';
}

function formatOperatorTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-TW', { hour12: false });
}

function operatorAttr(value) {
  return operatorApp()?.esc(value || '').replace(/"/g, '&quot;') || '';
}

function compactText(value, maxLength = 700) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function setIssueResult(message, isError = false) {
  const app = operatorApp();
  const result = app?.$('issueResult');
  if (!app || !result) return;
  app.setResultMessage(result, `upload-result show${isError ? ' error' : ''}`, message);
}

function setOperatorSuggestion(content, isError = false, asHtml = false) {
  const app = operatorApp();
  const box = app?.$('operatorSuggestionBox');
  if (!app || !box) return;
  box.className = isError ? 'operator-suggestion error' : 'operator-suggestion';
  if (asHtml) {
    box.innerHTML = content;
  } else {
    box.textContent = content;
  }
}

function findManualField(text, label) {
  const sections = 'Parameters|Explanation|Reaction|Remedy|Program continuation|Manual Page';
  const regex = new RegExp(`(?:^|\\n)\\s*${label}\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*(?:${sections})\\s*:|$)`, 'i');
  return text.match(regex)?.[1]?.trim() || '';
}

function renderSuggestionSection(title, body, tone = '') {
  const app = operatorApp();
  if (!body) return '';
  return `<div class="suggestion-section ${tone}">
    <div class="suggestion-label">${app.esc(title)}</div>
    <div class="suggestion-text">${app.esc(body)}</div>
  </div>`;
}

function renderLookupSuggestion(lookup) {
  const app = operatorApp();
  const text = lookup.text || '';
  const remedy = findManualField(text, 'Remedy');
  const reaction = findManualField(text, 'Reaction');
  const explanation = findManualField(text, 'Explanation');
  const program = findManualField(text, 'Program continuation');
  const fallback = !remedy && !reaction && !explanation ? compactText(text, 520) : '';

  return `<div class="suggestion-card">
    <div class="suggestion-head">
      <div>
        <div class="suggestion-kicker">Alarm ${app.esc(lookup.code || '')}</div>
        <div class="suggestion-title">${app.esc(lookup.title || '手冊命中')}</div>
      </div>
      <div class="suggestion-page">P.${app.esc(lookup.page || '-')}</div>
    </div>
    ${renderSuggestionSection('建議處置', compactText(remedy || fallback), 'primary')}
    ${renderSuggestionSection('系統反應', compactText(reaction), 'warn')}
    ${renderSuggestionSection('說明', compactText(explanation))}
    ${renderSuggestionSection('程式延續', compactText(program))}
  </div>`;
}

function renderChatSuggestion(content) {
  const app = operatorApp();
  const clean = String(content || '')
    .replace(/<!--\s*(?:PAGE|TITLE|CODE):[^>]+-->/g, '')
    .replace(/\*\*/g, '')
    .trim();
  const lines = clean.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const title = lines.shift() || 'LLM 建議';
  const body = lines.length ? lines.join('\n') : clean;

  return `<div class="suggestion-card">
    <div class="suggestion-head">
      <div>
        <div class="suggestion-kicker">LLM Suggestion</div>
        <div class="suggestion-title">${app.esc(title)}</div>
      </div>
    </div>
    ${renderSuggestionSection('排除方向', compactText(body, 900), 'primary')}
  </div>`;
}

function getOperatorQuestion() {
  const app = operatorApp();
  const code = app.$('issueAlarmCode').value.trim();
  const description = app.$('issueDescription').value.trim();
  return code && description ? `Alarm ${code}: ${description}` : code || description;
}

async function lookupOperatorSuggestion() {
  const app = operatorApp();
  if (!app) return;

  const manual = app.$('issueManual').value;
  const question = getOperatorQuestion();
  if (!question) {
    setOperatorSuggestion('請先輸入警報代碼或問題描述。', true);
    return;
  }

  setOperatorSuggestion('查詢中...');
  app.setState('operatorLastQuestion', question);
  app.setState('operatorLastSuggestion', '');
  app.$('operatorSuggestionFeedback').style.display = 'none';

  try {
    const alarmCode = app.$('issueAlarmCode').value.trim();
    if (/^\d{2,6}$/.test(alarmCode)) {
      const lookup = await app.apiJson(`/v1/${encodeURIComponent(manual)}/lookup?code=${encodeURIComponent(alarmCode)}`);
      if (lookup.found) {
        const suggestion = [`Alarm ${lookup.code}`, lookup.title || '', lookup.text || ''].filter(Boolean).join('\n\n');
        app.setState('operatorLastSuggestion', suggestion);
        setOperatorSuggestion(renderLookupSuggestion(lookup), false, true);
        app.$('operatorSuggestionFeedback').style.display = 'flex';
        return;
      }
      if (!app.$('issueDescription').value.trim()) {
        setOperatorSuggestion(`找不到 Alarm ${alarmCode}。請補充現場症狀後再查詢 RAG 建議。`, true);
        return;
      }
    }

    const data = await app.apiJson(`/v1/${encodeURIComponent(manual)}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [{ role: 'user', content: question }],
        stream: false,
        temperature: 0.1,
        max_tokens: 700,
      }),
    });
    const suggestion = data?.choices?.[0]?.message?.content || '';
    if (!suggestion) throw new Error('LLM 沒有回傳內容');
    app.setState('operatorLastSuggestion', suggestion);
    setOperatorSuggestion(renderChatSuggestion(suggestion), false, true);
    app.$('operatorSuggestionFeedback').style.display = 'flex';
  } catch (error) {
    setOperatorSuggestion(app.formatError(error, '查詢建議失敗'), true);
  }
}

async function sendOperatorSuggestionFeedback(type) {
  const app = operatorApp();
  if (!app) return;

  try {
    await app.apiJson('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: app.getState('operatorLastQuestion') || getOperatorQuestion(),
        collection: app.$('issueManual').value,
        feedback: type,
        alarm_code: app.$('issueAlarmCode').value.trim(),
        user_id: app.currentUserId?.() || '',
        role: app.currentUserRole?.() || 'operator',
      }),
    });
    setIssueResult(type === 'good' ? '已記錄：建議有幫助。' : '已記錄：建議需改善。');
  } catch (error) {
    setIssueResult(app.formatError(error, '記錄回饋失敗'), true);
  }
}

function readOperatorPayload(createWorkOrder) {
  const app = operatorApp();
  return {
    source: 'operator',
    manual: app.$('issueManual').value,
    machine_id: app.$('issueMachine').value.trim(),
    line_id: app.$('issueLine').value.trim(),
    alarm_code: app.$('issueAlarmCode').value.trim(),
    description: app.$('issueDescription').value.trim(),
    severity: app.$('issueSeverity').value,
    created_by: app.currentUserId?.() || '',
    rag_suggestion: app.getState('operatorLastSuggestion') || '',
    create_work_order: createWorkOrder,
  };
}

function getOperatorUserId() {
  const app = operatorApp();
  return app?.currentUserId?.() || 'operator';
}

function getOperatorIssue(issueId) {
  return operatorApp()?.getState('operatorIssuesById')?.[issueId] || null;
}

function getOperatorWorkOrder(workOrderId) {
  return operatorApp()?.getState('operatorWorkOrdersById')?.[workOrderId] || null;
}

function setOperatorIssue(issue) {
  const app = operatorApp();
  if (!app || !issue?.issue_id) return;
  const issuesById = app.getState('operatorIssuesById') || {};
  app.setState('operatorIssuesById', { ...issuesById, [issue.issue_id]: issue });
}

function setOperatorWorkOrder(order) {
  const app = operatorApp();
  if (!app || !order?.id) return;
  const ordersById = app.getState('operatorWorkOrdersById') || {};
  app.setState('operatorWorkOrdersById', { ...ordersById, [order.id]: order });
}

function bindOperatorIssueCardEvents() {
  const app = operatorApp();
  const list = app?.$('issueList');
  if (!app || !list || list.dataset.bound === 'true') return;
  list.dataset.bound = 'true';

  list.addEventListener('click', (event) => {
    const card = event.target.closest('[data-issue-id]');
    if (card && list.contains(card)) openOperatorIssueModal(card.dataset.issueId || '');
  });

  list.addEventListener('keydown', (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    const card = event.target.closest('[data-issue-id]');
    if (!card || !list.contains(card)) return;
    event.preventDefault();
    openOperatorIssueModal(card.dataset.issueId || '');
  });
}

function canEditOperatorCore(issue) {
  return false;
}

function canEscalateOperatorIssue(issue) {
  return issue?.status === 'open' && !issue.work_order_id;
}

function canAddOperatorNote(issue) {
  return issue && !['verified', 'cancelled'].includes(issue.status);
}

function operatorIssueGroup(issue) {
  if (issue.status === 'completed') return 'confirm';
  if (issue.work_order_id || ['assigned', 'in_progress'].includes(issue.status)) return 'maintenance';
  return 'intake';
}

function renderOperatorIssueCard(issue) {
  const app = operatorApp();
  const workOrderBadge = issue.work_order_id
    ? `<span class="wo-badge">工單 #${app.esc(issue.work_order_id)}</span>`
    : '<span class="wo-badge">尚未通知維修</span>';
  const editedBadge = issue.updated_at && issue.updated_at !== issue.created_at
    ? `<span class="wo-badge">更新 ${app.esc(formatOperatorTime(issue.updated_at))}</span>`
    : '';

  return `<div class="wo-card operator-issue-card" data-issue-id="${operatorAttr(issue.issue_id)}" tabindex="0" role="button">
    <div class="wo-code">${app.esc(issue.issue_id)} ${issue.alarm_code ? `| Alarm ${app.esc(issue.alarm_code)}` : ''}</div>
    <div class="wo-desc">${app.esc(issue.description || '')}</div>
    <div class="wo-meta">
      <span class="wo-badge">機台 ${app.esc(issue.machine_id || 'N/A')}</span>
      <span class="wo-badge">產線 ${app.esc(issue.line_id || 'N/A')}</span>
      <span class="wo-badge">狀態 ${app.esc(issueStatusLabel(issue.status))}</span>
      <span class="wo-badge">嚴重度 ${app.esc(issueSeverityLabel(issue.severity))}</span>
      <span class="wo-badge">來源 ${app.esc(issue.source || 'operator')}</span>
      ${workOrderBadge}
      ${editedBadge}
    </div>
  </div>`;
}

function renderOperatorIssueGroup(title, issues) {
  const cards = issues.length ? issues.map(renderOperatorIssueCard).join('') : '<div class="wo-empty">目前沒有項目</div>';
  return `<div class="operator-issue-group">
    <div class="wo-col-head">
      <span class="wo-col-title">${title}</span>
      <span class="wo-col-count">${issues.length}</span>
    </div>
    ${cards}
  </div>`;
}

function renderOperatorIssues(issues, stats) {
  const app = operatorApp();
  const list = app?.$('issueList');
  if (!app || !list) return;

  const today = new Date().toISOString().slice(0, 10);
  const visible = (issues || []).filter((issue) => !['verified', 'cancelled'].includes(issue.status));
  const issuesById = (issues || []).reduce((acc, issue) => {
    acc[issue.issue_id] = issue;
    return acc;
  }, {});
  app.setState('operatorIssuesById', issuesById);
  app.$('issueOpenCount').textContent = String(stats?.unresolved ?? visible.length);
  app.$('issueTodayCount').textContent = String(visible.filter((issue) => String(issue.created_at || '').startsWith(today)).length);
  app.$('issueWorkOrderCount').textContent = String(visible.filter((issue) => issue.work_order_id).length);
  app.$('issueLineLabel').textContent = app.$('issueLine').value.trim() || 'ALL';
  app.$('operatorUserLabel').textContent = `${app.currentUserId?.() || 'operator'} (${app.currentUserRole?.() || ''})`;

  if (!visible.length) {
    list.innerHTML = '<div class="wo-empty">目前沒有未解決問題</div>';
    return;
  }

  const grouped = visible.reduce((acc, issue) => {
    acc[operatorIssueGroup(issue)].push(issue);
    return acc;
  }, { intake: [], maintenance: [], confirm: [] });
  list.innerHTML = `<div class="operator-issue-groups">
    ${renderOperatorIssueGroup('待通知維修', grouped.intake)}
    ${renderOperatorIssueGroup('維修處理中', grouped.maintenance)}
    ${renderOperatorIssueGroup('等待確認', grouped.confirm)}
  </div>`;
}

async function createOperatorIssue(createWorkOrder) {
  const app = operatorApp();
  if (!app) return;

  const payload = readOperatorPayload(createWorkOrder);
  if (!payload.machine_id || !payload.description) {
    setIssueResult('請填寫機台與問題描述。', true);
    return;
  }

  try {
    const data = await app.apiJson('/issues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.status !== 'ok') throw new Error(data.message || '建立問題失敗');

    app.$('issueAlarmCode').value = '';
    app.$('issueDescription').value = '';
    const suffix = data.work_order ? `，並建立工單 #${data.work_order.id}` : '';
    setIssueResult(`已建立問題 ${data.issue.issue_id}${suffix}`);
    setOperatorIssue(data.issue);
    if (data.work_order) setOperatorWorkOrder(data.work_order);
    await loadOperatorIssues();
  } catch (error) {
    setIssueResult(app.formatError(error, '建立問題失敗'), true);
  }
}

function renderOperatorNotes(issue) {
  const app = operatorApp();
  const notes = Array.isArray(issue.operator_notes) ? issue.operator_notes : [];
  if (!notes.length) return '<div class="wo-empty">尚無補充說明</div>';

  return notes.map((note) => `<div class="operator-note-item">
    <div class="operator-note-meta">${app.esc(note.created_by || 'operator')} · ${app.esc(formatOperatorTime(note.created_at))}</div>
    <div class="operator-note-text">${app.esc(note.note || '')}</div>
  </div>`).join('');
}

function renderOperatorIssueHistory(issue) {
  const app = operatorApp();
  const history = Array.isArray(issue.issue_history) ? issue.issue_history : [];
  if (!history.length) return '<div class="wo-empty">No audit events yet.</div>';

  return history.slice().reverse().map((event) => {
    const statusText = event.from_status || event.to_status
      ? `${event.from_status || '-'} -> ${event.to_status || '-'}`
      : '';
    const changes = window.AlarmAudit?.renderChanges(app, event) || '';
    return `<div class="audit-event">
      <div class="audit-dot"></div>
      <div class="audit-body">
        <div class="audit-title">${app.esc(event.action || 'updated')} ${statusText ? `<span>${app.esc(statusText)}</span>` : ''}</div>
        <div class="audit-meta">${app.esc(event.user_id || 'system')} · ${app.esc(formatOperatorTime(event.created_at))}</div>
        ${changes}
      </div>
    </div>`;
  }).join('');
}

function renderOperatorLinkedWorkOrder(issue) {
  const app = operatorApp();
  if (!issue.work_order_id) return '';

  const order = getOperatorWorkOrder(issue.work_order_id);
  if (!order) {
    return `<div class="operator-detail-block">
      <div class="operator-detail-label">Linked work order</div>
      <div class="operator-detail-text">#${app.esc(issue.work_order_id)}</div>
    </div>`;
  }

  return `<div class="operator-detail-block">
    <div class="operator-detail-label">Linked work order</div>
    <div class="operator-detail-grid">
      <div><div class="operator-detail-label">工單</div><div class="operator-detail-value">#${app.esc(order.id)}</div></div>
      <div><div class="operator-detail-label">狀態</div><div class="operator-detail-value">${app.esc(workOrderStatusLabel(order.status))}</div></div>
      <div><div class="operator-detail-label">負責人</div><div class="operator-detail-value">${app.esc(order.assigned_to || '-')}</div></div>
      <div><div class="operator-detail-label">完成時間</div><div class="operator-detail-value">${app.esc(formatOperatorTime(order.completed_at))}</div></div>
    </div>
    ${order.resolution ? `<div class="operator-detail-text">${app.esc(order.resolution)}</div>` : ''}
  </div>`;
}

function renderOperatorIssueFields(issue, isEditing) {
  const app = operatorApp();
  if (!isEditing) {
    const original = issue.original_description || issue.description || '';
    return `<div class="operator-detail-grid">
      <div><div class="operator-detail-label">機台</div><div class="operator-detail-value">${app.esc(issue.machine_id || '-')}</div></div>
      <div><div class="operator-detail-label">產線</div><div class="operator-detail-value">${app.esc(issue.line_id || '-')}</div></div>
      <div><div class="operator-detail-label">警報代碼</div><div class="operator-detail-value">${app.esc(issue.alarm_code || '-')}</div></div>
      <div><div class="operator-detail-label">嚴重度</div><div class="operator-detail-value">${app.esc(issueSeverityLabel(issue.severity))}</div></div>
      <div><div class="operator-detail-label">建立時間</div><div class="operator-detail-value">${app.esc(formatOperatorTime(issue.created_at))}</div></div>
      <div><div class="operator-detail-label">更新時間</div><div class="operator-detail-value">${app.esc(formatOperatorTime(issue.updated_at))}</div></div>
    </div>
    <div class="operator-detail-block">
      <div class="operator-detail-label">原始描述</div>
      <div class="operator-detail-text">${app.esc(original)}</div>
    </div>
    ${issue.rag_suggestion ? `<div class="operator-detail-block">
      <div class="operator-detail-label">初步建議</div>
      <div class="operator-detail-text compact">${app.esc(compactText(issue.rag_suggestion))}</div>
    </div>` : ''}
    ${issue.resolution_summary ? `<div class="operator-detail-block">
      <div class="operator-detail-label">維修處理結果</div>
      <div class="operator-detail-text">${app.esc(issue.resolution_summary)}</div>
    </div>` : ''}
    ${renderOperatorLinkedWorkOrder(issue)}`;
  }

  return `<div class="wo-modal-grid">
    <div class="wo-field"><label class="wo-field-label">機台</label><input class="wo-input" id="editIssueMachine" value="${operatorAttr(issue.machine_id || '')}"></div>
    <div class="wo-field"><label class="wo-field-label">產線</label><input class="wo-input" id="editIssueLine" value="${operatorAttr(issue.line_id || '')}"></div>
    <div class="wo-field"><label class="wo-field-label">警報代碼</label><input class="wo-input" id="editIssueAlarmCode" value="${operatorAttr(issue.alarm_code || '')}"></div>
    <div class="wo-field"><label class="wo-field-label">嚴重度</label><select class="wo-select" id="editIssueSeverity">
      ${['low', 'medium', 'high', 'critical'].map((severity) => `<option value="${severity}" ${issue.severity === severity ? 'selected' : ''}>${app.esc(issueSeverityLabel(severity))}</option>`).join('')}
    </select></div>
    <div class="wo-field wo-field-full"><label class="wo-field-label">問題描述</label><textarea class="wo-text operator-edit-text" id="editIssueDescription">${app.esc(issue.description || '')}</textarea></div>
  </div>`;
}

function renderOperatorIssueActions(issue, isEditing) {
  const app = operatorApp();
  if (isEditing) {
    return `<button class="wo-btn" onclick="saveOperatorIssueEdit(${app.toJsArg(issue.issue_id)})">儲存修改</button>
      <button class="wo-btn alt" onclick="openOperatorIssueModal(${app.toJsArg(issue.issue_id)})">取消</button>`;
  }

  return [
    canEditOperatorCore(issue) ? `<button class="wo-btn" onclick="openOperatorIssueModal(${app.toJsArg(issue.issue_id)}, 'edit')">編輯</button>` : '',
    canEscalateOperatorIssue(issue) ? `<button class="wo-btn alt" onclick="escalateOperatorIssue(${app.toJsArg(issue.issue_id)})">通知維修</button>` : '',
    canAddOperatorNote(issue) ? `<button class="wo-btn alt" onclick="addOperatorIssueNote(${app.toJsArg(issue.issue_id)})">新增補充</button>` : '',
    issue.status === 'completed' ? `<button class="wo-btn" onclick="verifyOperatorIssue(${app.toJsArg(issue.issue_id)})">確認已解決</button>` : '',
    issue.status === 'completed' ? `<button class="wo-btn alt" onclick="reopenOperatorIssue(${app.toJsArg(issue.issue_id)})">重新開啟</button>` : '',
  ].filter(Boolean).join('');
}

function openOperatorIssueModal(issueId, mode = 'view') {
  const app = operatorApp();
  const issue = getOperatorIssue(issueId);
  const modal = app?.$('operatorIssueModal');
  const detail = app?.$('operatorIssueDetail');
  if (!app || !modal || !detail || !issue) return;

  const isEditing = mode === 'edit' && canEditOperatorCore(issue);
  app.$('operatorModalTitle').textContent = `${issue.issue_id} ${issue.alarm_code ? `| Alarm ${issue.alarm_code}` : ''}`;
  app.$('operatorModalSub').textContent = `狀態 ${issueStatusLabel(issue.status)} · ${issue.work_order_id ? `工單 #${issue.work_order_id}` : '尚未通知維修'}`;
  detail.innerHTML = `${renderOperatorIssueFields(issue, isEditing)}
    <div class="operator-detail-block">
      <div class="operator-detail-label">補充說明</div>
      <div id="operatorNotesList">${renderOperatorNotes(issue)}</div>
      ${canAddOperatorNote(issue) && !isEditing ? '<textarea class="wo-text operator-note-input" id="operatorNoteInput" placeholder="補充現場狀況、復現步驟或重新開啟原因"></textarea>' : ''}
    </div>
    <div class="operator-detail-block">
      <div class="operator-detail-label">Audit timeline</div>
      <div class="audit-list">${renderOperatorIssueHistory(issue)}</div>
    </div>
    <div class="wo-actions">${renderOperatorIssueActions(issue, isEditing)}</div>`;
  modal.classList.add('show');
}

function closeOperatorIssueModal() {
  operatorApp()?.$('operatorIssueModal')?.classList.remove('show');
}

async function patchOperatorIssue(issueId, payload, successMessage) {
  const app = operatorApp();
  if (!app) return null;

  try {
    const data = await app.apiJson(`/issues/${encodeURIComponent(issueId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, updated_by: getOperatorUserId() }),
    });
    if (data.status !== 'ok') throw new Error(data.message || '更新問題失敗');
    setOperatorIssue(data.issue);
    if (data.work_order) setOperatorWorkOrder(data.work_order);
    setIssueResult(successMessage);
    await loadOperatorIssues();
    if (['verified', 'cancelled'].includes(data.issue.status)) {
      closeOperatorIssueModal();
    } else {
      openOperatorIssueModal(issueId);
    }
    return data.issue;
  } catch (error) {
    setIssueResult(app.formatError(error, '更新問題失敗'), true);
    return null;
  }
}

async function saveOperatorIssueEdit(issueId) {
  const app = operatorApp();
  const issue = getOperatorIssue(issueId);
  if (!app || !issue || !canEditOperatorCore(issue)) return;

  const payload = {
    machine_id: app.$('editIssueMachine').value.trim(),
    line_id: app.$('editIssueLine').value.trim(),
    alarm_code: app.$('editIssueAlarmCode').value.trim(),
    severity: app.$('editIssueSeverity').value,
    description: app.$('editIssueDescription').value.trim(),
  };
  if (!payload.machine_id || !payload.description) {
    setIssueResult('請填寫機台與問題描述。', true);
    return;
  }
  await patchOperatorIssue(issueId, payload, `已更新問題 ${issueId}`);
}

async function addOperatorIssueNote(issueId) {
  const app = operatorApp();
  const note = app?.$('operatorNoteInput')?.value.trim();
  if (!app || !note) {
    setIssueResult('請輸入補充說明。', true);
    return;
  }
  await patchOperatorIssue(issueId, { operator_note: note }, `已新增 ${issueId} 的補充說明`);
}

async function verifyOperatorIssue(issueId) {
  await patchOperatorIssue(issueId, { status: 'verified', operator_note: 'Operator confirmed resolved.' }, `已確認 ${issueId} 解決`);
}

async function reopenOperatorIssue(issueId) {
  const app = operatorApp();
  const note = app?.$('operatorNoteInput')?.value.trim();
  if (!note) {
    setIssueResult('請填寫重新開啟原因。', true);
    app?.$('operatorNoteInput')?.focus();
    return;
  }
  await patchOperatorIssue(issueId, { status: 'open', operator_note: note }, `已重新開啟 ${issueId}`);
}

async function escalateOperatorIssue(issueId) {
  const app = operatorApp();
  if (!app) return;

  try {
    const data = await app.apiJson(`/issues/${encodeURIComponent(issueId)}/escalate`, { method: 'POST' });
    if (data.status !== 'ok') throw new Error(data.message || '通知維修失敗');
    setOperatorIssue(data.issue);
    if (data.work_order) setOperatorWorkOrder(data.work_order);
    const suffix = data.created ? `，建立工單 #${data.work_order.id}` : `，已連結工單 #${data.work_order_id}`;
    setIssueResult(`已通知維修 ${issueId}${suffix}`);
    await loadOperatorIssues();
    openOperatorIssueModal(issueId);
  } catch (error) {
    setIssueResult(app.formatError(error, '通知維修失敗'), true);
  }
}

function operatorResolvedItem(issue) {
  return {
    id: issue.issue_id,
    title: `${issue.issue_id}${issue.alarm_code ? ` | Alarm ${issue.alarm_code}` : ''}`,
    description: issue.resolution_summary || issue.description || '',
    status: issue.status,
    machine: issue.machine_id || 'N/A',
    line: issue.line_id || 'N/A',
    date: issue.completed_at || issue.updated_at || issue.created_at,
    workOrderId: issue.work_order_id || '',
  };
}

function renderOperatorResolvedItem(item) {
  const app = operatorApp();
  return `<div class="resolved-item">
    <div class="wo-code">${app.esc(item.title)}</div>
    <div class="wo-desc">${app.esc(item.description.slice(0, 140) || '無描述')}</div>
    <div class="wo-meta">
      <span class="wo-badge">${app.esc(issueStatusLabel(item.status))}</span>
      <span class="wo-badge">機台 ${app.esc(item.machine)}</span>
      <span class="wo-badge">產線 ${app.esc(item.line)}</span>
      ${item.workOrderId ? `<span class="wo-badge">工單 #${app.esc(item.workOrderId)}</span>` : ''}
      <button class="wo-btn alt" onclick="openOperatorIssueModal(${app.toJsArg(item.id)})">查看</button>
    </div>
  </div>`;
}

function renderOperatorResolvedCalendar() {
  const app = operatorApp();
  if (!app?.renderResolvedCalendar) return;

  const issues = app.getState('operatorIssues') || [];
  const items = issues
    .filter((issue) => ['completed', 'verified'].includes(issue.status))
    .map(operatorResolvedItem);
  const monthKey = app.getState('operatorResolvedMonth') || app.calendarMonthKey();
  const selectedDate = app.getState('operatorResolvedDate') || app.calendarDateKey(new Date().toISOString());
  app.renderResolvedCalendar({
    calendarEl: app.$('operatorResolvedCalendar'),
    listEl: app.$('operatorResolvedList'),
    items,
    monthKey,
    selectedDate,
    renderItem: renderOperatorResolvedItem,
    emptyText: '這天沒有已解決問題。',
  });
}

function bindOperatorResolvedCalendar() {
  const app = operatorApp();
  const calendar = app?.$('operatorResolvedCalendar');
  if (!app || !calendar || calendar.dataset.bound === 'true') return;
  calendar.dataset.bound = 'true';

  calendar.addEventListener('click', (event) => {
    const actionButton = event.target.closest('[data-calendar-action]');
    if (actionButton) {
      const offset = actionButton.dataset.calendarAction === 'prev' ? -1 : 1;
      app.setState('operatorResolvedMonth', app.shiftCalendarMonth(app.getState('operatorResolvedMonth'), offset));
      renderOperatorResolvedCalendar();
      return;
    }
    const dayButton = event.target.closest('[data-calendar-date]');
    if (dayButton) {
      app.setState('operatorResolvedDate', dayButton.dataset.calendarDate);
      renderOperatorResolvedCalendar();
    }
  });
}

async function loadOperatorIssues() {
  const app = operatorApp();
  if (!app) return;

  const line = app.$('issueLine')?.value.trim();
  const query = new URLSearchParams();
  if (line) query.set('line_id', line);

  try {
    const [list, stats, ordersData] = await Promise.all([
      app.apiJson(`/issues?${query.toString()}`),
      app.apiJson('/issues/stats'),
      app.apiJson('/work-orders'),
    ]);
    const ordersById = (ordersData.orders || []).reduce((acc, order) => {
      acc[order.id] = order;
      return acc;
    }, {});
    app.setState('operatorIssues', list.issues || []);
    app.setState('operatorWorkOrdersById', ordersById);
    renderOperatorIssues(list.issues || [], stats);
    renderOperatorResolvedCalendar();
  } catch (error) {
    const listEl = app.$('issueList');
    if (listEl) {
      listEl.innerHTML = `<div class="wo-empty">${app.esc(app.formatError(error, '載入問題失敗'))}</div>`;
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = operatorApp();
  if (!app) return;

  app.$('issueLine')?.addEventListener('change', loadOperatorIssues);
  app.$('issueUser')?.addEventListener('change', loadOperatorIssues);
  if (app.$('issueUser')) {
    app.$('issueUser').value = app.currentUserId?.() || '';
    app.$('issueUser').disabled = true;
  }
  if (app.currentUser?.()?.role !== 'operator' && app.$('issueLine')) {
    app.$('issueLine').value = '';
  }
  bindOperatorIssueCardEvents();
  bindOperatorResolvedCalendar();
  loadOperatorIssues();
});

window.createOperatorIssue = createOperatorIssue;
window.loadOperatorIssues = loadOperatorIssues;
window.lookupOperatorSuggestion = lookupOperatorSuggestion;
window.sendOperatorSuggestionFeedback = sendOperatorSuggestionFeedback;
window.openOperatorIssueModal = openOperatorIssueModal;
window.closeOperatorIssueModal = closeOperatorIssueModal;
window.saveOperatorIssueEdit = saveOperatorIssueEdit;
window.addOperatorIssueNote = addOperatorIssueNote;
window.escalateOperatorIssue = escalateOperatorIssue;
window.verifyOperatorIssue = verifyOperatorIssue;
window.reopenOperatorIssue = reopenOperatorIssue;
