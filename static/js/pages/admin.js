function adminApp() {
  return window.AlarmApp || null;
}

const ADMIN_ROLES = ['operator', 'maintenance', 'supervisor', 'admin'];
const ADMIN_IMPORT_TYPES = new Set(['excel', 'import', 'workorder']);
const ADMIN_COLLECTIONS = ['808d', '840d', '840dsl', 'furnace_b85t'];
const ADMIN_SECTIONS = new Set(['overview', 'users', 'data', 'knowledge', 'quality', 'sessions', 'settings']);
const ADMIN_NEW_USER_FIELDS = [
  'adminNewUserId',
  'adminNewUserName',
  'adminNewUserTeam',
  'adminNewUserScope',
  'adminNewUserPassword',
];

function adminCsvCell(value) {
  const text = String(value ?? '').replace(/"/g, '""');
  return `"${text}"`;
}

function downloadAdminCsv(filename, rows) {
  if (!rows.length) {
    setAdminResult('adminKbResult', '沒有可匯出的資料', true);
    return;
  }
  const csv = rows.map((row) => row.map(adminCsvCell).join(',')).join('\n');
  const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function adminJsArg(value) {
  return adminAttr(JSON.stringify(String(value ?? '')));
}

function adminAttr(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function adminEmpty(className, message) {
  return `<div class="${className}">${message}</div>`;
}

function adminTime(value) {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString('zh-TW', { hour12: false });
}

function setAdminResult(id, message, isError = false) {
  const app = adminApp();
  const result = app?.$(id);
  if (!app || !result) {
    return;
  }
  app.setResultMessage(result, `upload-result show${isError ? ' error' : ''}`, message);
}

function activeAdminCollection() {
  return adminApp()?.$('adminKbCollection')?.value || '808d';
}

function activeAdminSectionFromHash() {
  const section = String(window.location.hash || '').replace(/^#/, '');
  return ADMIN_SECTIONS.has(section) ? section : 'overview';
}

function selectAdminSection(section, updateHash = true) {
  const nextSection = ADMIN_SECTIONS.has(section) ? section : 'overview';
  document.querySelectorAll('[data-admin-section]').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.adminSection === nextSection);
  });
  document.querySelectorAll('[data-admin-section-target]').forEach((button) => {
    const active = button.dataset.adminSectionTarget === nextSection;
    button.classList.toggle('active', active);
    if (active) {
      button.setAttribute('aria-current', 'page');
    } else {
      button.removeAttribute('aria-current');
    }
  });
  if (updateHash && window.location.hash !== `#${nextSection}`) {
    window.history.replaceState(null, '', `#${nextSection}`);
  }
}

function adminScopeList(value) {
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function assertAdminOk(data, fallbackMessage) {
  if (data.status !== 'ok') {
    throw new Error(data.message || fallbackMessage);
  }
  return data;
}

async function adminJson(path, options, fallbackMessage) {
  const app = adminApp();
  if (!app) {
    throw new Error(fallbackMessage);
  }
  const data = await app.apiJson(path, options);
  return assertAdminOk(data, fallbackMessage);
}

function clearAdminFields(ids) {
  const app = adminApp();
  ids.forEach((id) => {
    const input = app?.$(id);
    if (input) {
      input.value = '';
    }
  });
}

function countAdminRoles(users) {
  return users.reduce((counts, user) => {
    counts.set(user.role, (counts.get(user.role) || 0) + 1);
    return counts;
  }, new Map());
}

function filterAdminUsers(users) {
  const app = adminApp();
  if (!app) {
    return [];
  }
  const roleFilter = app.$('adminRoleFilter')?.value || '';
  const activeFilter = app.$('adminActiveFilter')?.value || '';
  const search = String(app.$('adminUserSearch')?.value || '').trim().toLowerCase();
  return users.filter((user) => {
    const active = user.active !== false;
    if (roleFilter && user.role !== roleFilter) {
      return false;
    }
    if (activeFilter === 'active' && !active) {
      return false;
    }
    if (activeFilter === 'inactive' && active) {
      return false;
    }
    if (!search) {
      return true;
    }
    const scope = Array.isArray(user.line_scope) ? user.line_scope.join(' ') : '';
    return [user.user_id, user.role, user.name, user.team, scope]
      .some((value) => String(value || '').toLowerCase().includes(search));
  });
}

function renderAdminRoleOptions(selectedRole) {
  return ADMIN_ROLES
    .map((role) => `<option value="${role}" ${selectedRole === role ? 'selected' : ''}>${role}</option>`)
    .join('');
}

function editedAdminUserPayload(userId) {
  const app = adminApp();
  const user = (app?.getState('adminUsers') || []).find((item) => String(item.user_id || '') === String(userId));
  return {
    name: app?.$(`adminName_${userId}`)?.value || '',
    team: app?.$(`adminTeam_${userId}`)?.value || '',
    role: app?.$(`adminRole_${userId}`)?.value || 'operator',
    line_scope: adminScopeList(app?.$(`adminScope_${userId}`)?.value),
    ...(user?.updated_at ? { expected_updated_at: user.updated_at } : {}),
  };
}

function newAdminUserPayload(userId) {
  const app = adminApp();
  const password = app?.$('adminNewUserPassword')?.value || '';
  return {
    user_id: userId,
    name: app?.$('adminNewUserName')?.value.trim() || userId,
    team: app?.$('adminNewUserTeam')?.value.trim() || '',
    role: app?.$('adminNewUserRole')?.value || 'operator',
    line_scope: adminScopeList(app?.$('adminNewUserScope')?.value),
    ...(password ? { password } : {}),
  };
}

function renderAdminUser(app, user) {
  const userId = String(user.user_id || '');
  const lineScope = Array.isArray(user.line_scope) ? user.line_scope.join(', ') : '';
  const active = user.active !== false;
  const safeId = adminAttr(userId);
  return `<div class="role-row compact">
    <div>
      <div class="wo-code">${app.esc(userId)} | ${app.esc(user.role)} ${active ? '' : '| inactive'}</div>
      <div class="wo-meta">
        <span class="wo-badge">${app.esc(user.name || '-')}</span>
        <span class="wo-badge">團隊 ${app.esc(user.team || '-')}</span>
        <span class="wo-badge">scope ${app.esc(lineScope || '-')}</span>
      </div>
      <div class="role-user-edit">
        <input class="wo-input" id="adminName_${safeId}" value="${adminAttr(user.name || '')}" placeholder="顯示名稱">
        <input class="wo-input" id="adminTeam_${safeId}" value="${adminAttr(user.team || '')}" placeholder="團隊">
        <select class="wo-select" id="adminRole_${safeId}">
          ${renderAdminRoleOptions(user.role)}
        </select>
        <input class="wo-input" id="adminScope_${safeId}" value="${adminAttr(lineScope)}" placeholder="LINE-A, LINE-B 或 *">
        <div class="wo-actions" style="margin-top:0">
          <button class="wo-btn alt" type="button" onclick="resetAdminPassword(${adminJsArg(userId)})">重設密碼</button>
          <button class="wo-btn alt" type="button" onclick="revokeAdminUserSessions(${adminJsArg(userId)})">撤銷 Session</button>
          <button class="wo-btn alt" type="button" onclick="saveAdminUser(${adminJsArg(userId)})">儲存</button>
          <button class="wo-btn ${active ? 'danger' : 'alt'}" type="button" onclick="toggleAdminUser(${adminJsArg(userId)}, ${active ? 'false' : 'true'})">${active ? '停用' : '啟用'}</button>
        </div>
      </div>
    </div>
  </div>`;
}

function renderAdminUsers(users) {
  const app = adminApp();
  const target = app?.$('adminUserList');
  if (!app || !target) {
    return;
  }
  if (!users.length) {
    target.innerHTML = adminEmpty('wo-empty', '沒有使用者');
    return;
  }
  const filteredUsers = filterAdminUsers(users);
  const roleCounts = countAdminRoles(users);
  target.innerHTML = `<div class="role-mini-grid">
    ${[...roleCounts].map(([role, count]) => `<div class="role-mini-card"><span>${app.esc(role)}</span><b>${count}</b><small>users</small></div>`).join('')}
  </div>
  <div class="wo-note">顯示 ${filteredUsers.length} / ${users.length}</div>
  ${filteredUsers.length ? filteredUsers.map((user) => renderAdminUser(app, user)).join('') : adminEmpty('wo-empty', '沒有符合條件的使用者')}`;
}

function renderAdminImportLog(entries) {
  const app = adminApp();
  const target = app?.$('adminImportLog');
  if (!app || !target) {
    return;
  }
  const imports = entries
    .filter((entry) => ADMIN_IMPORT_TYPES.has(String(entry.type || entry.source || '').toLowerCase()))
    .slice(-5)
    .reverse();
  if (!imports.length) {
    target.innerHTML = adminEmpty('wo-empty', '目前沒有匯入紀錄');
    return;
  }
  target.innerHTML = imports.map((entry) => `<div class="role-row compact">
    <div>
      <div class="wo-code">${app.esc(entry.filename || entry.title || entry.doc_id || 'import')}</div>
      <div class="wo-meta">
        <span class="wo-badge">${app.esc(adminTime(entry.time || entry.imported_at))}</span>
        <span class="wo-badge">count ${app.esc(String(entry.total || entry.count || 0))}</span>
      </div>
    </div>
  </div>`).join('');
}

function renderAdminKbSummary(app, summary) {
  return `
    <div class="role-kpi-card"><span>文件數</span><b>${app.esc(String(summary?.documents ?? 0))}</b><small>${app.esc(activeAdminCollection().toUpperCase())}</small></div>
    <div class="role-kpi-card"><span>片段數</span><b>${app.esc(String(summary?.sections ?? 0))}</b><small>已建立索引片段</small></div>
    <div class="role-kpi-card"><span>狀態</span><b>${summary?.ready ? 'READY' : 'WAIT'}</b><small>collection health</small></div>`;
}

function renderAdminKbDocument(app, doc) {
  const docId = String(doc.doc_id || '');
  const deleteButton = doc.legacy
    ? ''
    : `<button class="wo-btn danger" type="button" onclick="deleteAdminKbDocument(${adminJsArg(docId)})">刪除</button>`;
  return `<div class="kb-doc-item">
    <div class="kb-doc-main">
      <div class="kb-doc-title">${app.esc(doc.filename || doc.title || docId || 'document')}</div>
      <div class="kb-doc-meta">
        <span class="kb-doc-badge">${app.esc(String(doc.kind || 'text').toUpperCase())}</span>
        <span class="kb-doc-badge">${app.esc(String(doc.sections || 0))} 個片段</span>
        <span class="kb-doc-badge">v${app.esc(String(doc.version ?? 1))}</span>
      </div>
      <div class="kb-doc-sub">ID: ${app.esc(docId || '-')}<br>Imported: ${app.esc(adminTime(doc.imported_at))}</div>
    </div>
    <div class="kb-doc-actions">${deleteButton}</div>
  </div>`;
}

function renderAdminKb(summary, documents) {
  const app = adminApp();
  const summaryEl = app?.$('adminKbSummary');
  const docsEl = app?.$('adminKbDocs');
  if (!app || !summaryEl || !docsEl) {
    return;
  }
  app.setState('adminKbDocuments', documents);
  summaryEl.innerHTML = renderAdminKbSummary(app, summary);
  if (!documents.length) {
    docsEl.innerHTML = adminEmpty('log-empty', '這個 collection 目前沒有文件');
    return;
  }
  docsEl.innerHTML = documents.map((doc) => renderAdminKbDocument(app, doc)).join('');
}

function renderAdminAudit(ingestEntries, settings) {
  const app = adminApp();
  const target = app?.$('adminAuditList');
  if (!app || !target) {
    return;
  }
  const events = [
    ...(ingestEntries || []).map((entry) => ({
      title: entry.action || entry.type || 'kb_event',
      actor: entry.updated_by || entry.user_id || 'system',
      time: entry.time || entry.imported_at,
      detail: entry.filename || entry.title || entry.doc_id || entry.collection || '',
    })),
    settings?.updated_at ? {
      title: 'settings_updated',
      actor: settings.updated_by || 'admin',
      time: settings.updated_at,
      detail: `default_manual=${settings.default_manual}, session_hours=${settings.session_hours}`,
    } : null,
  ].filter(Boolean).sort((left, right) => new Date(right.time || 0) - new Date(left.time || 0)).slice(0, 12);
  app.setState('adminAuditEvents', events);
  if (!events.length) {
    target.innerHTML = adminEmpty('wo-empty', '尚無系統稽核事件');
    return;
  }
  target.innerHTML = events.map((event) => `<div class="audit-event">
    <div class="audit-dot"></div>
    <div class="audit-body">
      <div class="audit-title">${app.esc(event.title)}</div>
      <div class="audit-meta">${app.esc(event.actor)} | ${app.esc(adminTime(event.time))}</div>
      <div class="audit-fields">${app.esc(event.detail)}</div>
    </div>
  </div>`).join('');
}

function renderAdminOpsSummary(users, ingestEntries, settings, feedbackStats, workStats) {
  const app = adminApp();
  const target = app?.$('adminOpsSummary');
  if (!app || !target) {
    return;
  }
  const activeUsers = users.filter((user) => user.active !== false).length;
  const inactiveUsers = users.length - activeUsers;
  const importCount = (ingestEntries || []).filter((entry) =>
    ADMIN_IMPORT_TYPES.has(String(entry.type || entry.source || '').toLowerCase()),
  ).length;
  target.innerHTML = `
    <div class="role-kpi-card"><span>啟用使用者</span><b>${activeUsers}</b><small>${inactiveUsers} 位停用</small></div>
    <div class="role-kpi-card"><span>未結工單</span><b>${app.esc(String(workStats?.open_orders ?? 0))}</b><small>${app.esc(String(workStats?.overdue_open ?? 0))} 筆逾期</small></div>
    <div class="role-kpi-card"><span>回饋率</span><b>${app.esc(feedbackStats?.rate || '0%')}</b><small>${app.esc(String(feedbackStats?.total ?? 0))} 筆紀錄</small></div>
    <div class="role-kpi-card"><span>匯入紀錄</span><b>${importCount}</b><small>設定 ${app.esc(settings?.updated_at ? adminTime(settings.updated_at) : '-')}</small></div>`;
}

function normalizeAdminQualityItems(orders, feedbackEntries) {
  const orderItems = (orders || []).map((order) => {
    const correctness = order.llm_correctness || '';
    const coverage = order.llm_coverage || '';
    const missingInfo = String(order.llm_missing_info || '').trim();
    const reviewStatus = order.kb_review_status || 'not_ready';
    const hasGap = ['incorrect', 'partially_correct'].includes(correctness) ||
      ['missing_steps', 'missing_source'].includes(coverage) ||
      Boolean(missingInfo) ||
      ['pending_review', 'needs_revision', 'validation_failed'].includes(reviewStatus);
    const isCandidate = Boolean(order.kb_candidate);
    return {
      type: 'work_order',
      time: order.updated_at || order.completed_at || order.created_at,
      alarm_code: order.alarm_code || '',
      collection: order.manual || '',
      issue_id: order.issue_id || '',
      work_order_id: order.id || '',
      answer_id: order.rag_answer_id || '',
      query: order.description || '',
      feedback: '',
      role: order.updated_by || order.assigned_to || '',
      correctness,
      coverage,
      missing_info: missingInfo,
      expected_fix: order.llm_expected_fix || order.resolution || order.repair_action || '',
      kb_candidate: isCandidate,
      kb_review_status: reviewStatus,
      kb_review_note: order.kb_review_note || '',
      kb_reviewed_by: order.kb_reviewed_by || '',
      kb_duplicate_of: order.kb_duplicate_of || '',
      has_gap: hasGap,
      status: order.status || '',
      source: order.source || 'workorder',
    };
  });
  const feedbackItems = (feedbackEntries || []).map((entry) => {
    const correctness = entry.correctness || '';
    const coverage = entry.coverage || '';
    const missingInfo = String(entry.missing_info || '').trim();
    return {
      type: 'feedback',
      time: entry.time,
      alarm_code: entry.alarm_code || '',
      collection: entry.collection || '',
      issue_id: entry.issue_id || '',
      work_order_id: entry.work_order_id || '',
      answer_id: entry.answer_id || '',
      query: entry.query || '',
      feedback: entry.feedback || '',
      role: entry.role || entry.user_id || '',
      correctness,
      coverage,
      missing_info: missingInfo,
      expected_fix: entry.expected_fix || '',
      kb_candidate: Boolean(entry.kb_candidate),
      kb_review_status: '',
      kb_review_note: '',
      kb_reviewed_by: '',
      kb_duplicate_of: '',
      has_gap: entry.feedback === 'bad' ||
        ['incorrect', 'partially_correct'].includes(correctness) ||
        ['missing_steps', 'missing_source'].includes(coverage) ||
        Boolean(missingInfo),
      status: '',
      source: 'feedback',
    };
  });
  return [...orderItems, ...feedbackItems]
    .sort((left, right) => new Date(right.time || 0) - new Date(left.time || 0));
}

function adminQualityFilteredItems() {
  const app = adminApp();
  const allItems = app?.getState('adminQualityItems') || [];
  const filter = app?.$('adminQualityFilter')?.value || 'gaps';
  const search = String(app?.$('adminQualitySearch')?.value || '').trim().toLowerCase();
  return allItems.filter((item) => {
    if (filter === 'gaps' && !item.has_gap) {
      return false;
    }
    if (filter === 'candidates' && !['pending_review', 'needs_revision', 'validation_failed'].includes(item.kb_review_status)) {
      return false;
    }
    if (filter === 'feedback' && item.type !== 'feedback') {
      return false;
    }
    if (!search) {
      return true;
    }
    return [
      item.alarm_code,
      item.collection,
      item.issue_id,
      item.work_order_id,
      item.answer_id,
      item.query,
      item.missing_info,
      item.expected_fix,
      item.correctness,
      item.coverage,
      item.role,
      item.kb_review_status,
      item.kb_review_note,
      item.kb_duplicate_of,
    ].some((value) => String(value || '').toLowerCase().includes(search));
  });
}

function qualityLabel(value, fallback = '未評估') {
  const labels = {
    correct: '正確',
    partially_correct: '部分正確',
    incorrect: '不正確',
    unknown: '未知',
    complete: '完整',
    missing_steps: '缺步驟',
    missing_source: '缺來源',
    not_applicable: '不適用',
    good: '有幫助',
    bad: '需改善',
    not_ready: '尚未就緒',
    pending_review: '待審核',
    needs_revision: '退回補充',
    rejected: '不採用',
    ingested: '已寫入',
    validation_failed: '寫入失敗',
  };
  return value ? labels[value] || String(value).replace(/_/g, ' ') : fallback;
}

function renderAdminQualityItem(app, item) {
  const riskClass = item.has_gap ? 'quality-risk' : item.kb_candidate ? 'quality-candidate' : '';
  const answerAction = item.answer_id
    ? `<button class="wo-btn alt" type="button" onclick="AnswerTrace.open(${adminJsArg(item.answer_id)})">查看原回答</button>`
    : '';
  const titleParts = [
    item.alarm_code ? `Alarm ${item.alarm_code}` : item.type,
    item.collection ? String(item.collection).toUpperCase() : '',
    item.work_order_id ? `WO ${item.work_order_id}` : '',
  ].filter(Boolean);
  const detail = item.missing_info || item.expected_fix || item.query || '沒有詳細內容';
  const reviewActions = item.type === 'work_order' &&
    ['pending_review', 'needs_revision', 'validation_failed'].includes(item.kb_review_status)
    ? `<button class="wo-btn" type="button" onclick="reviewAdminKnowledge(${adminJsArg(item.work_order_id)}, 'approve')">核准寫入</button>
       <button class="wo-btn alt" type="button" onclick="reviewAdminKnowledge(${adminJsArg(item.work_order_id)}, 'needs_revision')">退回補充</button>
       <button class="wo-btn danger" type="button" onclick="reviewAdminKnowledge(${adminJsArg(item.work_order_id)}, 'reject')">不採用</button>`
    : '';
  const action = reviewActions || (item.work_order_id
    ? `<button class="wo-btn alt" type="button" onclick="selectAdminSection('data')">查看工單</button>`
    : `<button class="wo-btn alt" type="button" onclick="selectAdminSection('knowledge')">補知識庫</button>`);
  return `<div class="role-row quality-row ${riskClass}">
    <div>
      <div class="wo-code">${app.esc(titleParts.join(' | ') || 'RAG 回饋')}</div>
      <div class="wo-desc">${app.esc(detail)}</div>
      <div class="wo-meta">
        <span class="wo-badge">${app.esc(item.type === 'work_order' ? '工單' : '回饋')}</span>
        <span class="wo-badge">正確性 ${app.esc(qualityLabel(item.correctness))}</span>
        <span class="wo-badge">涵蓋度 ${app.esc(qualityLabel(item.coverage))}</span>
        <span class="wo-badge">回饋 ${app.esc(item.feedback || '-')}</span>
        <span class="wo-badge">候選知識 ${item.kb_candidate ? '是' : '否'}</span>
        ${item.kb_review_status ? `<span class="wo-badge">審核狀態 ${app.esc(qualityLabel(item.kb_review_status))}</span>` : ''}
        ${item.kb_reviewed_by ? `<span class="wo-badge">審核者 ${app.esc(item.kb_reviewed_by)}</span>` : ''}
        ${item.kb_duplicate_of ? `<span class="wo-badge">可能重複 ${app.esc(item.kb_duplicate_of)}</span>` : ''}
        ${item.answer_id ? `<span class="wo-badge">Answer ${app.esc(item.answer_id)}</span>` : ''}
        <span class="wo-badge">${app.esc(adminTime(item.time))}</span>
      </div>
      ${item.kb_review_note ? `<div class="wo-note">審核備註：${app.esc(item.kb_review_note)}</div>` : ''}
    </div>
    <div class="role-row-actions">${answerAction}${action}</div>
  </div>`;
}

function renderAdminQualitySummary(feedbackStats, workOrders, qualityItems) {
  const app = adminApp();
  const target = app?.$('adminQualitySummary');
  if (!app || !target) {
    return;
  }
  const gaps = qualityItems.filter((item) => item.has_gap).length;
  const candidates = qualityItems.filter((item) =>
    ['pending_review', 'needs_revision', 'validation_failed'].includes(item.kb_review_status),
  ).length;
  const evaluatedOrders = (workOrders || []).filter((order) => order.llm_correctness || order.llm_coverage).length;
  target.innerHTML = `
    <div class="role-kpi-card"><span>有幫助比例</span><b>${app.esc(feedbackStats?.rate || '0%')}</b><small>${app.esc(String(feedbackStats?.total ?? 0))} 筆回饋</small></div>
    <div class="role-kpi-card"><span>正確率</span><b>${app.esc(feedbackStats?.correctness_rate || '0%')}</b><small>${app.esc(String(evaluatedOrders))} 張已評估工單</small></div>
    <div class="role-kpi-card"><span>涵蓋率</span><b>${app.esc(feedbackStats?.coverage_rate || '0%')}</b><small>${app.esc(String(feedbackStats?.coverage_total ?? 0))} 筆回饋評估</small></div>
    <div class="role-kpi-card"><span>待處理缺口</span><b>${gaps}</b><small>需改善、錯誤、部分正確、缺漏</small></div>
    <div class="role-kpi-card"><span>候選知識</span><b>${candidates}</b><small>待審核的工單或回饋</small></div>`;
}

function renderAdminQuality(feedbackStats, workOrders) {
  const app = adminApp();
  const list = app?.$('adminQualityList');
  const feedbackList = app?.$('adminQualityFeedbackList');
  if (!app || !list || !feedbackList) {
    return;
  }
  const qualityItems = normalizeAdminQualityItems(workOrders, feedbackStats?.entries || []);
  app.setState('adminQualityItems', qualityItems);
  renderAdminQualitySummary(feedbackStats, workOrders, qualityItems);

  const filtered = adminQualityFilteredItems();
  list.innerHTML = filtered.length
    ? filtered.slice(0, 30).map((item) => renderAdminQualityItem(app, item)).join('')
    : adminEmpty('wo-empty', '目前沒有符合條件的 RAG 品質項目');

  const recentFeedback = (feedbackStats?.entries || []).slice().reverse();
  feedbackList.innerHTML = recentFeedback.length
    ? recentFeedback.slice(0, 12).map((entry) => renderAdminQualityItem(app, normalizeAdminQualityItems([], [entry])[0])).join('')
    : adminEmpty('wo-empty', '尚無 RAG 回饋');
}

function exportAdminQualityCsv() {
  const items = adminQualityFilteredItems();
  downloadAdminCsv('rag-quality-review.csv', [
    ['type', 'time', 'alarm_code', 'collection', 'issue_id', 'work_order_id', 'answer_id', 'feedback', 'correctness', 'coverage', 'missing_info', 'expected_fix', 'kb_candidate', 'kb_review_status', 'kb_review_note', 'kb_reviewed_by', 'kb_duplicate_of', 'query'],
    ...items.map((item) => [
      item.type,
      adminTime(item.time),
      item.alarm_code,
      item.collection,
      item.issue_id,
      item.work_order_id,
      item.answer_id,
      item.feedback,
      item.correctness,
      item.coverage,
      item.missing_info,
      item.expected_fix,
      item.kb_candidate ? 'yes' : 'no',
      item.kb_review_status,
      item.kb_review_note,
      item.kb_reviewed_by,
      item.kb_duplicate_of,
      item.query,
    ]),
  ]);
}

async function reviewAdminKnowledge(workOrderId, action) {
  const app = adminApp();
  if (!app) {
    return;
  }
  let note = '';
  if (action === 'needs_revision') {
    note = window.prompt('請輸入需要 Maintenance 補充的內容：', '') || '';
    if (!note.trim()) {
      return;
    }
  } else if (action === 'reject') {
    note = window.prompt('不採用原因（選填）：', '') || '';
    if (!window.confirm(`確定不採用工單 ${workOrderId} 的候選知識？`)) {
      return;
    }
  } else if (!window.confirm(`核准工單 ${workOrderId} 並寫入 RAG 知識庫？`)) {
    return;
  }

  try {
    const data = await adminJson(`/work-orders/${encodeURIComponent(workOrderId)}/knowledge-review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, note }),
    }, '知識審核失敗');
    const messages = {
      approve: '已核准並寫入知識庫',
      needs_revision: '已退回 Maintenance 補充',
      reject: '已標記為不採用',
    };
    setAdminResult('adminQualityResult', `${messages[action]}：工單 ${workOrderId}`);
    await loadAdminConsole();
  } catch (error) {
    setAdminResult('adminQualityResult', app.formatError(error, '知識審核失敗'), true);
    await loadAdminConsole();
  }
}

function renderAdminKbHealth(collectionResults) {
  const app = adminApp();
  const target = app?.$('adminKbHealth');
  if (!app || !target) {
    return;
  }
  target.innerHTML = collectionResults.map((item) => {
    if (item.error) {
      return `<div class="admin-health-card"><b>ERR</b><span>${app.esc(item.collection.toUpperCase())}</span></div>`;
    }
    const summary = item.summary || {};
    const vectorPoints = summary.vector_points ?? 0;
    const sections = summary.bm25_sections ?? summary.sections ?? 0;
    const vectorPercent = summary.vector_coverage_percent ?? 0;
    const vectorState = summary.vector_ready ? 'VECTOR OK' : 'VECTOR GAP';
    return `<div class="admin-health-card">
      <b>${summary.ready ? 'READY' : 'WAIT'}</b>
      <span>${app.esc(item.collection.toUpperCase())}: ${app.esc(String(summary.documents ?? 0))} 文件 / ${app.esc(String(summary.sections ?? 0))} 片段</span>
      <small>${app.esc(vectorState)} ${app.esc(String(vectorPoints))}/${app.esc(String(sections))} (${app.esc(String(vectorPercent))}%)</small>
    </div>`;
  }).join('');
}

async function loadAdminKbHealth() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const results = await Promise.all(ADMIN_COLLECTIONS.map(async (collection) => {
    try {
      const data = await app.apiJson(`/v1/${encodeURIComponent(collection)}/documents`);
      return { collection, summary: data.summary || {}, documents: data.documents || [] };
    } catch (error) {
      return { collection, error };
    }
  }));
  app.setState('adminKbHealth', results);
  renderAdminKbHealth(results);
}

function exportAdminUsersCsv() {
  const users = filterAdminUsers(adminApp()?.getState('adminUsers') || []);
  downloadAdminCsv('admin-users.csv', [
    ['user_id', 'name', 'role', 'team', 'active', 'line_scope'],
    ...users.map((user) => [
      user.user_id,
      user.name,
      user.role,
      user.team,
      user.active !== false ? 'active' : 'inactive',
      Array.isArray(user.line_scope) ? user.line_scope.join('|') : '',
    ]),
  ]);
}

function exportAdminAuditCsv() {
  const events = adminApp()?.getState('adminAuditEvents') || [];
  downloadAdminCsv('admin-audit.csv', [
    ['time', 'actor', 'title', 'detail'],
    ...events.map((event) => [adminTime(event.time), event.actor, event.title, event.detail]),
  ]);
}

function renderAdminSessions(sessions) {
  const app = adminApp();
  const target = app?.$('adminSessionList');
  if (!app || !target) {
    return;
  }
  if (!sessions.length) {
    target.innerHTML = adminEmpty('wo-empty', '目前沒有有效 Session');
    return;
  }
  target.innerHTML = sessions.map((session) => `<div class="role-row">
    <div>
      <div class="wo-code">${app.esc(session.user_id || '-')} | ${app.esc(session.role || '-')}</div>
      <div class="wo-meta">
        <span class="wo-badge">token ${app.esc(session.token_prefix || '-')}</span>
        <span class="wo-badge">建立 ${app.esc(adminTime(session.created_at))}</span>
        <span class="wo-badge">到期 ${app.esc(adminTime(session.expires_at))}</span>
      </div>
    </div>
    <div class="role-row-actions">
      <button class="wo-btn danger" type="button" onclick="revokeAdminSession(${adminJsArg(session.token_prefix)})">撤銷</button>
    </div>
  </div>`).join('');
}

async function loadAdminSessions() {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const data = await adminJson('/sessions', undefined, 'Session 載入失敗');
    app.setState('adminSessions', data.sessions || []);
    renderAdminSessions(data.sessions || []);
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Session 載入失敗'), true);
  }
}

async function revokeAdminSession(tokenPrefix) {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const data = await adminJson(`/sessions/${encodeURIComponent(tokenPrefix)}`, { method: 'DELETE' }, '撤銷失敗');
    setAdminResult('adminKbResult', `已撤銷 ${data.revoked || 0} 個 Session`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Session 撤銷失敗'), true);
  }
}

async function loadAdminSettings() {
  const app = adminApp();
  if (!app) {
    return null;
  }
  try {
    const data = await adminJson('/system-settings', undefined, 'settings load failed');
    const settings = data.settings || {};
    app.$('adminDefaultManual').value = settings.default_manual || '808d';
    app.$('adminSessionHours').value = String(settings.session_hours || 12);
    app.$('adminAllowOperatorReopen').checked = Boolean(settings.allow_operator_reopen);
    app.setState('adminSettingsRevision', settings.revision || '');
    setAdminResult('adminSettingsResult', settings.updated_by ? `設定已載入，最後更新者 ${settings.updated_by}` : '設定已載入');
    return settings;
  } catch (error) {
    setAdminResult('adminSettingsResult', app.formatError(error, '設定載入失敗'), true);
    return null;
  }
}

async function saveAdminSettings() {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const data = await adminJson('/system-settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        default_manual: app.$('adminDefaultManual').value,
        session_hours: Number(app.$('adminSessionHours').value || 12),
        allow_operator_reopen: app.$('adminAllowOperatorReopen').checked,
        expected_revision: app.getState('adminSettingsRevision') || '',
      }),
    }, '設定儲存失敗');
    app.setState('adminSettingsRevision', data.settings.revision || '');
    setAdminResult('adminSettingsResult', `設定已儲存，updated_by=${data.settings.updated_by || app.currentUserId()}`);
    loadAdminConsole();
  } catch (error) {
    setAdminResult('adminSettingsResult', app.formatError(error, '設定儲存失敗'), true);
  }
}

async function loadAdminKb() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const collection = activeAdminCollection();
  const requestSeq = Number(app.getState('adminKbRequestSeq') || 0) + 1;
  app.setState('adminKbRequestSeq', requestSeq);
  try {
    const data = await app.apiJson(`/v1/${encodeURIComponent(collection)}/documents`);
    if (app.getState('adminKbRequestSeq') !== requestSeq || activeAdminCollection() !== collection) {
      return;
    }
    renderAdminKb(data.summary || null, data.documents || []);
  } catch (error) {
    if (app.getState('adminKbRequestSeq') === requestSeq) {
      setAdminResult('adminKbResult', app.formatError(error, 'KB 載入失敗'), true);
    }
  }
}

async function ingestAdminText() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const text = app.$('adminIngestText').value.trim();
  if (!text) {
    setAdminResult('adminKbResult', '請先輸入要寫入知識庫的內容', true);
    return;
  }
  try {
    const data = await adminJson(`/v1/${encodeURIComponent(activeAdminCollection())}/ingest-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        code: app.$('adminIngestCode').value.trim(),
        title: app.$('adminIngestTitle').value.trim(),
        source: 'admin',
      }),
    }, '寫入失敗');
    app.$('adminIngestText').value = '';
    app.$('adminIngestCode').value = '';
    app.$('adminIngestTitle').value = '';
    setAdminResult('adminKbResult', `已寫入 ${activeAdminCollection().toUpperCase()}，目前 ${data.total_in_collection || 0} 筆`);
    await loadAdminKb();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '寫入知識庫失敗'), true);
  }
}

async function uploadAdminPdf(input) {
  const app = adminApp();
  const file = input.files[0];
  if (!app || !file) {
    return;
  }
  const formData = new FormData();
  formData.append('file', file);
  setAdminResult('adminKbResult', `上傳中：${file.name}`);
  try {
    const data = await adminJson(`/v1/${encodeURIComponent(activeAdminCollection())}/ingest`, {
      method: 'POST',
      body: formData,
    }, 'PDF 上傳失敗');
    setAdminResult('adminKbResult', `PDF 已寫入 ${data.collection.toUpperCase()}，新增 ${data.total_added || 0} 個片段`);
    await loadAdminKb();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'PDF 上傳失敗'), true);
  } finally {
    input.value = '';
  }
}

async function deleteAdminKbDocument(docId) {
  const app = adminApp();
  if (!app || !window.confirm(`刪除文件 ${docId}？`)) {
    return;
  }
  try {
    const document = (app.getState('adminKbDocuments') || []).find((item) => String(item.doc_id || '') === String(docId));
    const revision = document?.revision || '';
    if (!revision) {
      throw new Error('文件版本不存在，請重新載入後再試');
    }
    await adminJson(
      `/v1/${encodeURIComponent(activeAdminCollection())}/documents/${encodeURIComponent(docId)}?expected_revision=${encodeURIComponent(revision)}`,
      { method: 'DELETE' },
      '刪除失敗',
    );
    setAdminResult('adminKbResult', `已刪除 ${docId}`);
    await loadAdminKb();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '刪除文件失敗'), true);
  }
}

async function rebuildAdminKb() {
  const app = adminApp();
  const collection = activeAdminCollection();
  if (!app || !window.confirm(`重建 ${collection.toUpperCase()} 索引？`)) {
    return;
  }
  try {
    const data = await rebuildAdminCollection(collection);
    setAdminResult('adminKbResult', `重建${data.state || '完成'}：${collection.toUpperCase()}，片段=${data.sections || data.total_sections || 0}`);
    await loadAdminKb();
    await loadAdminKbHealth();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '重建索引失敗'), true);
  }
}

async function rebuildAdminCollection(collection) {
  const app = adminApp();
  if (!app) {
    throw new Error('重建失敗');
  }
  const data = await app.apiJson(`/v1/${encodeURIComponent(collection)}/rebuild`, { method: 'POST' });
  if (!['accepted', 'ok'].includes(data.status)) {
    throw new Error(data.message || '重建失敗');
  }
  if (data.status !== 'accepted' || !data.job_id) {
    return data;
  }
  return pollAdminRebuildJob(collection, data.job_id);
}

async function pollAdminRebuildJob(collection, jobId) {
  let latest = null;
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    latest = await adminJson(
      `/v1/${encodeURIComponent(collection)}/rebuild/${encodeURIComponent(jobId)}`,
      undefined,
      '重建狀態讀取失敗',
    );
    setAdminResult(
      'adminKbResult',
      `正在重建 ${collection.toUpperCase()} (${latest.phase || latest.state}) ${latest.percent || 0}% ` +
      `${latest.processed_sections || 0}/${latest.total_sections || latest.sections || 0}`,
    );
    if (['completed', 'failed', 'cancelled'].includes(latest.state)) {
      if (latest.state !== 'completed') {
        throw new Error(latest.error || `重建 ${latest.state}`);
      }
      return latest;
    }
  }
}

async function rebuildAllAdminKb() {
  const app = adminApp();
  if (!app || !window.confirm('確定要重建全部知識庫索引？')) {
    return;
  }
  const results = [];
  for (const collection of ADMIN_COLLECTIONS) {
    try {
      const data = await rebuildAdminCollection(collection);
      results.push(`${collection}:${data.sections || data.total_sections || 0}`);
    } catch (_) {
      results.push(`${collection}:ERR`);
    }
  }
  setAdminResult('adminKbResult', `重建完成：${results.join(', ')}`);
  await loadAdminKb();
  await loadAdminKbHealth();
}

function exportAdminKbCsv() {
  const collection = activeAdminCollection();
  const documents = adminApp()?.getState('adminKbDocuments') || [];
  downloadAdminCsv(`${collection}-documents.csv`, [
    ['collection', 'doc_id', 'title', 'filename', 'kind', 'sections', 'version', 'imported_at'],
    ...documents.map((doc) => [
      collection,
      doc.doc_id,
      doc.title,
      doc.filename,
      doc.kind,
      doc.sections,
      doc.version,
      doc.imported_at,
    ]),
  ]);
}

async function uploadAdminExcel(input) {
  const app = adminApp();
  const file = input.files[0];
  const result = app?.$('adminExcelResult');
  if (!app || !file || !result) {
    return;
  }
  const formData = new FormData();
  formData.append('file', file);
  result.className = 'excel-result show';
  result.textContent = `上傳中：${file.name}`;
  try {
    const data = await adminJson('/work-orders/import-excel', { method: 'POST', body: formData }, '匯入失敗');
    result.className = 'excel-result show ok';
    result.textContent = `匯入完成：新增 ${data.imported || data.created || 0} 筆，略過 ${data.skipped || 0} 筆`;
    await loadAdminConsole();
  } catch (error) {
    result.className = 'excel-result show err';
    result.textContent = app.formatError(error, 'Excel 匯入失敗');
  } finally {
    input.value = '';
  }
}

async function patchAdminUser(userId, payload) {
  const app = adminApp();
  if (!app) {
    return;
  }
  const user = (app.getState('adminUsers') || []).find((item) => String(item.user_id || '') === String(userId));
  const body = {
    ...payload,
    ...(payload.expected_updated_at || !user?.updated_at ? {} : { expected_updated_at: user.updated_at }),
  };
  try {
    await adminJson(`/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '使用者更新失敗');
    await loadAdminConsole();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '使用者更新失敗'), true);
  }
}

async function saveAdminUser(userId) {
  const app = adminApp();
  if (!app) {
    return;
  }
  await patchAdminUser(userId, editedAdminUserPayload(userId));
}

async function toggleAdminUser(userId, active) {
  await patchAdminUser(userId, { active });
}

async function resetAdminPassword(userId) {
  const app = adminApp();
  if (!app) {
    return;
  }
  const password = window.prompt(`${userId} 的新密碼`, '');
  if (!password) {
    return;
  }
  try {
    await adminJson(`/users/${encodeURIComponent(userId)}/password`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }, '密碼重設失敗');
    setAdminResult('adminKbResult', `${userId} 密碼已重設，既有 Session 已撤銷`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '密碼重設失敗'), true);
  }
}

async function revokeAdminUserSessions(userId) {
  const app = adminApp();
  if (!app || !window.confirm(`確定要撤銷 ${userId} 的所有有效 Session？`)) {
    return;
  }
  try {
    const data = await adminJson(`/users/${encodeURIComponent(userId)}/sessions`, {
      method: 'DELETE',
    }, '使用者 Session 撤銷失敗');
    setAdminResult('adminKbResult', `已撤銷 ${userId} 的 ${data.revoked || 0} 個 Session`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '使用者 Session 撤銷失敗'), true);
  }
}

async function createAdminUser() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const userId = app.$('adminNewUserId')?.value.trim() || '';
  if (!userId) {
    setAdminResult('adminKbResult', '請輸入 user_id', true);
    return;
  }
  try {
    await adminJson('/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newAdminUserPayload(userId)),
    }, '建立使用者失敗');
    clearAdminFields(ADMIN_NEW_USER_FIELDS);
    setAdminResult('adminKbResult', `已建立使用者 ${userId}`);
    await loadAdminConsole();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '建立使用者失敗'), true);
  }
}

async function loadAdminConsole() {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const [usersData, ingestLog, settings, feedbackStats, workStats, workOrdersData] = await Promise.all([
      app.apiJson('/users'),
      app.apiJson('/ingest-log').catch(() => ({ entries: [] })),
      loadAdminSettings(),
      app.apiJson('/feedback/stats').catch(() => ({ total: 0, rate: '0%' })),
      app.apiJson('/work-orders/stats').catch(() => ({ open_orders: 0, overdue_open: 0 })),
      app.apiPaged('/work-orders/page', 'orders').catch(() => ({ orders: [] })),
    ]);
    const users = usersData.users || [];
    const entries = ingestLog.entries || [];
    const workOrders = workOrdersData.orders || [];
    app.patchState({
      adminUsers: users,
      adminIngestEntries: entries,
      adminSettings: settings,
      adminFeedbackStats: feedbackStats,
      adminWorkOrders: workOrders,
    });
    renderAdminOpsSummary(users, entries, settings, feedbackStats, workStats);
    renderAdminUsers(users);
    renderAdminImportLog(entries);
    renderAdminAudit(entries, settings);
    renderAdminQuality(feedbackStats, workOrders);
    await loadAdminKb();
    await loadAdminKbHealth();
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Admin console 載入失敗'), true);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = adminApp();
  if (!app) {
    return;
  }
  app.initCommonPageBindings();
  app.$('adminUserLabel').textContent = `${app.currentUserId()} (${app.currentUserRole()})`;
  app.$('adminKbCollection')?.addEventListener('change', loadAdminKb);
  document.querySelectorAll('[data-admin-section-target]').forEach((button) => {
    button.addEventListener('click', () => selectAdminSection(button.dataset.adminSectionTarget));
  });
  window.addEventListener('hashchange', () => selectAdminSection(activeAdminSectionFromHash(), false));
  selectAdminSection(activeAdminSectionFromHash(), false);
  ['adminRoleFilter', 'adminActiveFilter', 'adminUserSearch'].forEach((id) => {
    app.$(id)?.addEventListener('input', () => renderAdminUsers(app.getState('adminUsers') || []));
    app.$(id)?.addEventListener('change', () => renderAdminUsers(app.getState('adminUsers') || []));
  });
  ['adminQualityFilter', 'adminQualitySearch'].forEach((id) => {
    app.$(id)?.addEventListener('input', () => renderAdminQuality(
      app.getState('adminFeedbackStats') || { entries: [] },
      app.getState('adminWorkOrders') || [],
    ));
    app.$(id)?.addEventListener('change', () => renderAdminQuality(
      app.getState('adminFeedbackStats') || { entries: [] },
      app.getState('adminWorkOrders') || [],
    ));
  });
  loadAdminConsole();
});

window.loadAdminConsole = loadAdminConsole;
window.uploadAdminExcel = uploadAdminExcel;
window.loadAdminKb = loadAdminKb;
window.ingestAdminText = ingestAdminText;
window.uploadAdminPdf = uploadAdminPdf;
window.deleteAdminKbDocument = deleteAdminKbDocument;
window.rebuildAdminKb = rebuildAdminKb;
window.loadAdminSettings = loadAdminSettings;
window.saveAdminSettings = saveAdminSettings;
window.saveAdminUser = saveAdminUser;
window.toggleAdminUser = toggleAdminUser;
window.createAdminUser = createAdminUser;
window.resetAdminPassword = resetAdminPassword;
window.revokeAdminUserSessions = revokeAdminUserSessions;
window.loadAdminSessions = loadAdminSessions;
window.revokeAdminSession = revokeAdminSession;
window.exportAdminUsersCsv = exportAdminUsersCsv;
window.exportAdminAuditCsv = exportAdminAuditCsv;
window.rebuildAllAdminKb = rebuildAllAdminKb;
window.exportAdminKbCsv = exportAdminKbCsv;
window.exportAdminQualityCsv = exportAdminQualityCsv;
window.selectAdminSection = selectAdminSection;
window.reviewAdminKnowledge = reviewAdminKnowledge;
