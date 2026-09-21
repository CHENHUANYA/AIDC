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
        <input class="wo-input" id="adminName_${safeId}" value="${adminAttr(user.name || '')}" placeholder="顯示名稱" aria-label="${safeId} 的顯示名稱">
        <input class="wo-input" id="adminTeam_${safeId}" value="${adminAttr(user.team || '')}" placeholder="團隊" aria-label="${safeId} 的團隊">
        <select class="wo-select" id="adminRole_${safeId}" aria-label="${safeId} 的角色">
          ${renderAdminRoleOptions(user.role)}
        </select>
        <input class="wo-input" id="adminScope_${safeId}" value="${adminAttr(lineScope)}" placeholder="LINE-A, LINE-B 或 *" aria-label="${safeId} 的產線範圍">
        <div class="wo-actions u-mt-0">
          <button class="wo-btn alt" type="button" data-on-click="resetAdminPassword" data-action-args="[${adminJsArg(userId)}]">重設密碼</button>
          <button class="wo-btn alt" type="button" data-on-click="revokeAdminUserSessions" data-action-args="[${adminJsArg(userId)}]">撤銷 Session</button>
          <button class="wo-btn alt" type="button" data-on-click="saveAdminUser" data-action-args="[${adminJsArg(userId)}]">儲存</button>
          <button class="wo-btn ${active ? 'danger' : 'alt'}" type="button" data-on-click="toggleAdminUser" data-action-args="[${adminJsArg(userId)}, ${active ? 'false' : 'true'}]">${active ? '停用' : '啟用'}</button>
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
    <div class="role-kpi-card"><span>📄 文件數</span><b>${app.esc(String(summary?.documents ?? 0))}</b><small>${app.esc(activeAdminCollection().toUpperCase())}</small></div>
    <div class="role-kpi-card"><span>🧩 可搜尋的內容片段</span><b>${app.esc(String(summary?.sections ?? 0))}</b><small>文件拆分後，供 AI 查找的內容</small></div>
    <div class="role-kpi-card"><span>${summary?.ready ? '✅' : '⏳'} 搜尋狀態</span><b>${summary?.ready ? '可查詢' : '尚未就緒'}</b><small>${summary?.ready ? '索引已建立，AI 可以查找這些資料' : '新增內容後，請按重新整理查看狀態'}</small></div>`;
}

function renderAdminKbDocument(app, doc) {
  const docId = String(doc.doc_id || '');
  const deleteButton = doc.legacy
    ? ''
    : `<button class="wo-btn danger" type="button" data-on-click="deleteAdminKbDocument" data-action-args="[${adminJsArg(docId)}]">刪除</button>`;
  return `<div class="kb-doc-item">
    <div class="kb-doc-main">
      <div class="kb-doc-title">${app.esc(doc.filename || doc.title || docId || 'document')}</div>
      <div class="kb-doc-meta">
        <span class="kb-doc-badge">${app.esc(({ pdf: 'PDF 文件', text: '文字紀錄', legacy: '既有資料' })[doc.kind] || doc.kind || '文字紀錄')}</span>
        <span class="kb-doc-badge">${app.esc(String(doc.sections || 0))} 個片段</span>
        <span class="kb-doc-badge">v${app.esc(String(doc.version ?? 1))}</span>
      </div>
      <div class="kb-doc-sub">加入時間：${app.esc(adminTime(doc.imported_at))}</div>
      <details class="admin-item-details"><summary>文件詳細資訊</summary><div class="kb-doc-sub">文件編號：${app.esc(docId || '-')}</div></details>
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
  renderAdminKbDocuments();
}

function renderAdminKbDocuments() {
  const app = adminApp();
  const target = app?.$('adminKbDocs');
  if (!app || !target) return;
  const documents = app.getState('adminKbDocuments') || [];
  const search = String(app.$('adminKbSearch')?.value || '').trim().toLowerCase();
  const filtered = documents.filter((doc) => [doc.filename, doc.title, doc.doc_id, doc.kind]
    .some((value) => String(value || '').toLowerCase().includes(search)));
  const count = app.$('adminKbDocCount');
  if (count) count.textContent = search ? `找到 ${filtered.length} / ${documents.length} 份文件` : `共 ${documents.length} 份文件`;
  target.innerHTML = filtered.length
    ? filtered.map((doc) => renderAdminKbDocument(app, doc)).join('')
    : `<div class="admin-empty-friendly"><div class="empty-icon">${search ? '🔍' : '📂'}</div><div class="empty-title">${search ? '找不到符合的文件' : '這個設備還沒有文件'}</div><div class="empty-desc">${search ? '試試其他關鍵字或清除搜尋。' : '從下方上傳 PDF 或新增文字，開始建立知識庫。AI 會從這些資料中學習。'}</div></div>`;
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
      version: order.version || 1,
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
      version: 0,
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

function qualityTagClass(value) {
  const map = {
    correct: 'tag-correct',
    complete: 'tag-correct',
    good: 'tag-correct',
    ingested: 'tag-correct',
    partially_correct: 'tag-partial',
    missing_steps: 'tag-partial',
    missing_source: 'tag-partial',
    needs_revision: 'tag-partial',
    incorrect: 'tag-incorrect',
    bad: 'tag-incorrect',
    validation_failed: 'tag-incorrect',
    rejected: 'tag-incorrect',
    pending_review: 'tag-pending',
    not_ready: 'tag-pending',
  };
  return map[value] || 'tag-default';
}

function renderAdminQualityItem(app, item) {
  const riskClass = item.has_gap ? 'quality-row quality-risk' : item.kb_candidate ? 'quality-row quality-candidate' : 'quality-row';
  const statusClass = item.has_gap ? 'status-risk' : ['pending_review', 'needs_revision', 'validation_failed'].includes(item.kb_review_status) ? 'status-review' : 'status-ok';
  const typeIcon = item.type === 'work_order' ? '🔧' : '💬';
  const typeIconClass = item.type === 'work_order' ? 'type-wo' : 'type-fb';
  const answerAction = item.answer_id
    ? `<button class="wo-btn alt" type="button" data-on-click="AnswerTrace.open" data-action-args="[${adminJsArg(item.answer_id)}]">📋 查看原回答</button>`
    : '';
  const titleParts = [
    item.alarm_code ? `⚡ 警報 ${item.alarm_code}` : item.type === 'work_order' ? '🔧 維修紀錄' : '💬 使用者回饋',
    item.collection ? String(item.collection).toUpperCase() : '',
    item.work_order_id ? `工單 ${item.work_order_id}` : '',
  ].filter(Boolean);
  const detail = item.missing_info || item.expected_fix || item.query || '沒有詳細內容';
  const reviewActions = item.type === 'work_order' &&
    ['pending_review', 'needs_revision', 'validation_failed'].includes(item.kb_review_status)
    ? `<button class="wo-btn" type="button" data-on-click="reviewAdminKnowledge" data-action-args="[${adminJsArg(item.work_order_id)}, &quot;approve&quot;, ${Number(item.version) || 1}]">✅ 核准加入知識庫</button>
       <button class="wo-btn alt" type="button" data-on-click="reviewAdminKnowledge" data-action-args="[${adminJsArg(item.work_order_id)}, &quot;needs_revision&quot;, ${Number(item.version) || 1}]">↩️ 退回補充</button>
       <button class="wo-btn danger" type="button" data-on-click="reviewAdminKnowledge" data-action-args="[${adminJsArg(item.work_order_id)}, &quot;reject&quot;, ${Number(item.version) || 1}]">❌ 不採用</button>`
    : '';
  const action = reviewActions || (item.work_order_id
    ? `<button class="wo-btn alt" type="button" data-on-click="selectAdminSection" data-action-args="[&quot;data&quot;]">查看工單</button>`
    : `<button class="wo-btn alt" type="button" data-on-click="selectAdminSection" data-action-args="[&quot;knowledge&quot;]">📚 補充知識內容</button>`);

  const correctnessTag = item.correctness ? qualityTagClass(item.correctness) : 'tag-default';
  const coverageTag = item.coverage ? qualityTagClass(item.coverage) : 'tag-default';

  return `<div class="role-row ${riskClass} quality-row-humanized ${statusClass}">
    <div>
      <div class="row-header">
        <span class="row-type-icon ${typeIconClass}">${typeIcon}</span>
        <span class="wo-code">${app.esc(titleParts.join(' | ') || 'RAG 回饋')}</span>
      </div>
      <div class="wo-desc">${app.esc(detail)}</div>
      <div class="wo-meta">
        <span class="tag-friendly ${item.type === 'work_order' ? 'tag-info' : 'tag-pending'}">${app.esc(item.type === 'work_order' ? '工單' : '回饋')}</span>
        <span class="tag-friendly ${correctnessTag}">正確性 ${app.esc(qualityLabel(item.correctness))}</span>
        <span class="tag-friendly ${coverageTag}">完整度 ${app.esc(qualityLabel(item.coverage))}</span>
        ${item.feedback ? `<span class="tag-friendly ${item.feedback === 'good' ? 'tag-correct' : 'tag-incorrect'}">${item.feedback === 'good' ? '👍' : '👎'} ${app.esc(qualityLabel(item.feedback))}</span>` : ''}
        ${item.kb_review_status ? `<span class="tag-friendly tag-pending">審核 ${app.esc(qualityLabel(item.kb_review_status))}</span>` : ''}
        ${item.kb_duplicate_of ? `<span class="tag-friendly tag-default">⚠️ 可能重複 ${app.esc(item.kb_duplicate_of)}</span>` : ''}
      </div>
      ${item.kb_review_note ? `<div class="wo-note">💬 審核備註：${app.esc(item.kb_review_note)}</div>` : ''}
      <details class="admin-item-details"><summary>📄 查看評估與處理詳情</summary>
        ${item.query ? `<p class="wo-desc">❓ 問題：${app.esc(item.query)}</p>` : ''}
        ${item.expected_fix ? `<p class="wo-desc">💡 建議補充／處理方式：${app.esc(item.expected_fix)}</p>` : ''}
        <p class="admin-help">🕐 更新時間：${app.esc(adminTime(item.time))}</p>
        ${item.answer_id ? `<p class="admin-help">🏷️ 回答編號：${app.esc(item.answer_id)}</p>` : ''}
        ${item.kb_reviewed_by ? `<p class="admin-help">👤 審核者：${app.esc(item.kb_reviewed_by)}</p>` : ''}
      </details>
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
  const rate = (value, total) => Number(total) > 0 ? app.esc(value || '0%') : '—';
  target.innerHTML = `
    <div class="quality-score-card"><div class="score-icon ${gaps > 0 ? 'warn' : 'ok'}">${gaps > 0 ? '⚠️' : '✅'}</div><span class="wo-field-label">待處理項目</span><b class="role-kpi-card b" style="font-size:28px;color:${gaps > 0 ? 'var(--org)' : 'var(--grn)'}">${gaps}</b><small class="wo-desc">需要改善或審核的紀錄</small></div>
    <div class="quality-score-card"><div class="score-icon ${candidates > 0 ? 'info' : 'ok'}">${candidates > 0 ? '📝' : '✅'}</div><span class="wo-field-label">待審核知識</span><b class="role-kpi-card b" style="font-size:28px;color:${candidates > 0 ? 'var(--acc)' : 'var(--grn)'}">${candidates}</b><small class="wo-desc">待確認、補充或重試的工單</small></div>
    <div class="quality-score-card"><div class="score-icon info">👍</div><span class="wo-field-label">回答有幫助</span><b class="role-kpi-card b" style="font-size:28px">${rate(feedbackStats?.rate, feedbackStats?.total)}</b><small class="wo-desc">${feedbackStats?.total ? `${Number(feedbackStats.total)} 筆使用者回饋` : '尚無使用者回饋'}</small></div>
    <div class="quality-score-card"><div class="score-icon info">✅</div><span class="wo-field-label">回答正確率</span><b class="role-kpi-card b" style="font-size:28px">${rate(feedbackStats?.correctness_rate, feedbackStats?.correctness_total)}</b><small class="wo-desc">${feedbackStats?.correctness_total ? `${Number(feedbackStats.correctness_total)} 筆正確性評估` : '尚無正確性評估'}</small></div>
    <div class="quality-score-card"><div class="score-icon info">📊</div><span class="wo-field-label">回答完整率</span><b class="role-kpi-card b" style="font-size:28px">${rate(feedbackStats?.coverage_rate, feedbackStats?.coverage_total)}</b><small class="wo-desc">${feedbackStats?.coverage_total ? `${Number(feedbackStats.coverage_total)} 筆完整度評估` : '尚無完整度評估'}</small></div>`;
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
  const filter = app.$('adminQualityFilter')?.value || 'gaps';
  const title = app.$('adminQualityListTitle');
  if (title) title.textContent = ({ gaps: '⚠️ 待處理項目', candidates: '📝 待審核知識', feedback: '💬 使用者回饋', all: '📊 全部品質紀錄' })[filter];
  const count = app.$('adminQualityCount');
  if (count) count.textContent = filtered.length > 30 ? `共 ${filtered.length} 筆，顯示最近 30 筆` : `共 ${filtered.length} 筆`;
  const searching = Boolean(app.$('adminQualitySearch')?.value.trim());
  const emptyIcon = { gaps: '✅', candidates: '📝', feedback: '💬', all: '📊' };
  const emptyTitle = searching ? '找不到符合的項目'
    : { gaps: '太棒了！目前沒有需要處理的項目', candidates: '目前沒有待審核的知識', feedback: '還沒收到使用者回饋', all: '還沒有品質紀錄' }[filter];
  const emptyDesc = searching ? '試試其他關鍵字或清除搜尋。'
    : { gaps: '收到需改善的回饋或待審核紀錄時，會自動出現在這裡。', candidates: '維修紀錄送審後，就可以在這裡確認內容、核准加入知識庫。', feedback: '使用者在 AI 回答下方點 👍 或 👎 後，就會顯示在這裡。', all: '收到回饋或工單評估後，就能看到品質分析結果。' }[filter];
  list.innerHTML = filtered.length
    ? filtered.slice(0, 30).map((item) => renderAdminQualityItem(app, item)).join('')
    : `<div class="admin-empty-friendly"><div class="empty-icon">${searching ? '🔍' : (emptyIcon[filter] || '📋')}</div><div class="empty-title">${emptyTitle}</div><div class="empty-desc">${emptyDesc}</div></div>`;

  const recentFeedback = (feedbackStats?.entries || []).slice().reverse();
  feedbackList.innerHTML = recentFeedback.length
    ? recentFeedback.slice(0, 12).map((entry) => renderAdminQualityItem(app, normalizeAdminQualityItems([], [entry])[0])).join('')
    : `<div class="admin-empty-friendly"><div class="empty-icon">💬</div><div class="empty-title">還沒有收到回饋</div><div class="empty-desc">使用者可以在 AI 回答下方點 👍 或 👎 來評價，回饋會即時顯示在這裡。</div></div>`;
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

async function reviewAdminKnowledge(workOrderId, action, version) {
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
      body: JSON.stringify({ action, note, version }),
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
      <button class="wo-btn danger" type="button" data-on-click="revokeAdminSession" data-action-args="[${adminJsArg(session.token_prefix)}]">撤銷</button>
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
  app.setState('adminKbDocuments', []);
  app.$('adminKbDocs').innerHTML = adminEmpty('log-empty', '正在載入文件…');
  app.$('adminKbSummary').innerHTML = adminEmpty('wo-empty', '正在載入知識庫概況…');
  if (app.$('adminKbDocCount')) app.$('adminKbDocCount').textContent = '';
  try {
    const data = await app.apiJson(`/v1/${encodeURIComponent(collection)}/documents`);
    if (app.getState('adminKbRequestSeq') !== requestSeq || activeAdminCollection() !== collection) {
      return;
    }
    renderAdminKb(data.summary || null, data.documents || []);
  } catch (error) {
    if (app.getState('adminKbRequestSeq') === requestSeq) {
      app.$('adminKbDocs').innerHTML = adminEmpty('log-empty', '文件載入失敗，請按「重新整理」再試一次。');
      app.$('adminKbSummary').innerHTML = '';
      setAdminResult('adminKbResult', app.formatError(error, '知識庫載入失敗'), true);
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
    setAdminResult('adminUserResult', app.formatError(error, '使用者更新失敗'), true);
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
  const user = (app.getState('adminUsers') || []).find((item) => String(item.user_id || '') === String(userId));
  const body = {
    password,
    ...(user?.updated_at ? { expected_updated_at: user.updated_at } : {}),
  };
  try {
    await adminJson(`/users/${encodeURIComponent(userId)}/password`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, '密碼重設失敗');
    setAdminResult('adminUserResult', `${userId} 密碼已重設，既有 Session 已撤銷`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminUserResult', app.formatError(error, '密碼重設失敗'), true);
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
    setAdminResult('adminUserResult', `已撤銷 ${userId} 的 ${data.revoked || 0} 個 Session`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminUserResult', app.formatError(error, '使用者 Session 撤銷失敗'), true);
  }
}

async function createAdminUser() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const userId = app.$('adminNewUserId')?.value.trim() || '';
  if (!userId) {
    setAdminResult('adminUserResult', '請輸入 user_id', true);
    return;
  }
  try {
    await adminJson('/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newAdminUserPayload(userId)),
    }, '建立使用者失敗');
    clearAdminFields(ADMIN_NEW_USER_FIELDS);
    setAdminResult('adminUserResult', `已建立使用者 ${userId}`);
    await loadAdminConsole();
  } catch (error) {
    setAdminResult('adminUserResult', app.formatError(error, '建立使用者失敗'), true);
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
  app.$('adminKbSearch')?.addEventListener('input', renderAdminKbDocuments);
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
