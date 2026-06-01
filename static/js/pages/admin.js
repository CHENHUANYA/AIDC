function adminApp() {
  return window.AlarmApp || null;
}

const ADMIN_ROLES = ['operator', 'maintenance', 'supervisor', 'admin'];
const ADMIN_IMPORT_TYPES = new Set(['excel', 'import', 'workorder']);
const ADMIN_COLLECTIONS = ['808d', '840d', '840dsl', 'furnace_b85t'];
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
    setAdminResult('adminKbResult', 'No data to export', true);
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
  return {
    name: app?.$(`adminName_${userId}`)?.value || '',
    team: app?.$(`adminTeam_${userId}`)?.value || '',
    role: app?.$(`adminRole_${userId}`)?.value || 'operator',
    line_scope: adminScopeList(app?.$(`adminScope_${userId}`)?.value),
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
        <span class="wo-badge">team ${app.esc(user.team || '-')}</span>
        <span class="wo-badge">scope ${app.esc(lineScope || '-')}</span>
      </div>
      <div class="role-user-edit">
        <input class="wo-input" id="adminName_${safeId}" value="${adminAttr(user.name || '')}" placeholder="Display name">
        <input class="wo-input" id="adminTeam_${safeId}" value="${adminAttr(user.team || '')}" placeholder="Team">
        <select class="wo-select" id="adminRole_${safeId}">
          ${renderAdminRoleOptions(user.role)}
        </select>
        <input class="wo-input" id="adminScope_${safeId}" value="${adminAttr(lineScope)}" placeholder="LINE-A, LINE-B 或 *">
        <div class="wo-actions" style="margin-top:0">
          <button class="wo-btn alt" type="button" onclick="resetAdminPassword(${adminJsArg(userId)})">Reset Password</button>
          <button class="wo-btn alt" type="button" onclick="revokeAdminUserSessions(${adminJsArg(userId)})">Revoke Sessions</button>
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
    target.innerHTML = adminEmpty('wo-empty', '沒有 mock users');
    return;
  }
  const filteredUsers = filterAdminUsers(users);
  const roleCounts = countAdminRoles(users);
  target.innerHTML = `<div class="role-mini-grid">
    ${[...roleCounts].map(([role, count]) => `<div class="role-mini-card"><span>${app.esc(role)}</span><b>${count}</b><small>users</small></div>`).join('')}
  </div>
  <div class="wo-note">Showing ${filteredUsers.length} / ${users.length}</div>
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
    <div class="role-kpi-card"><span>Documents</span><b>${app.esc(String(summary?.documents ?? 0))}</b><small>${app.esc(activeAdminCollection().toUpperCase())}</small></div>
    <div class="role-kpi-card"><span>Sections</span><b>${app.esc(String(summary?.sections ?? 0))}</b><small>indexed chunks</small></div>
    <div class="role-kpi-card"><span>Status</span><b>${summary?.ready ? 'READY' : 'WAIT'}</b><small>collection health</small></div>`;
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
        <span class="kb-doc-badge">${app.esc(String(doc.sections || 0))} sections</span>
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
    <div class="role-kpi-card"><span>Active Users</span><b>${activeUsers}</b><small>${inactiveUsers} inactive</small></div>
    <div class="role-kpi-card"><span>Open Orders</span><b>${app.esc(String(workStats?.open_orders ?? 0))}</b><small>${app.esc(String(workStats?.overdue_open ?? 0))} overdue</small></div>
    <div class="role-kpi-card"><span>Feedback Rate</span><b>${app.esc(feedbackStats?.rate || '0%')}</b><small>${app.esc(String(feedbackStats?.total ?? 0))} records</small></div>
    <div class="role-kpi-card"><span>Imports</span><b>${importCount}</b><small>settings ${app.esc(settings?.updated_at ? adminTime(settings.updated_at) : '-')}</small></div>`;
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
    return `<div class="admin-health-card">
      <b>${summary.ready ? 'READY' : 'WAIT'}</b>
      <span>${app.esc(item.collection.toUpperCase())}: ${app.esc(String(summary.documents ?? 0))} docs / ${app.esc(String(summary.sections ?? 0))} sections</span>
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
    target.innerHTML = adminEmpty('wo-empty', 'No active sessions');
    return;
  }
  target.innerHTML = sessions.map((session) => `<div class="role-row">
    <div>
      <div class="wo-code">${app.esc(session.user_id || '-')} | ${app.esc(session.role || '-')}</div>
      <div class="wo-meta">
        <span class="wo-badge">token ${app.esc(session.token_prefix || '-')}</span>
        <span class="wo-badge">created ${app.esc(adminTime(session.created_at))}</span>
        <span class="wo-badge">expires ${app.esc(adminTime(session.expires_at))}</span>
      </div>
    </div>
    <div class="role-row-actions">
      <button class="wo-btn danger" type="button" onclick="revokeAdminSession(${adminJsArg(session.token_prefix)})">Revoke</button>
    </div>
  </div>`).join('');
}

async function loadAdminSessions() {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const data = await adminJson('/sessions', undefined, 'sessions load failed');
    app.setState('adminSessions', data.sessions || []);
    renderAdminSessions(data.sessions || []);
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Session load failed'), true);
  }
}

async function revokeAdminSession(tokenPrefix) {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const data = await adminJson(`/sessions/${encodeURIComponent(tokenPrefix)}`, { method: 'DELETE' }, 'revoke failed');
    setAdminResult('adminKbResult', `Revoked ${data.revoked || 0} session(s)`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Session revoke failed'), true);
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
      }),
    }, 'settings save failed');
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
  try {
    const data = await app.apiJson(`/v1/${encodeURIComponent(collection)}/documents`);
    renderAdminKb(data.summary || null, data.documents || []);
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'KB 載入失敗'), true);
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
    }, 'ingest failed');
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
    }, 'PDF upload failed');
    setAdminResult('adminKbResult', `PDF 已寫入 ${data.collection.toUpperCase()}，新增 ${data.total_added || 0} 個 sections`);
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
    await adminJson(
      `/v1/${encodeURIComponent(activeAdminCollection())}/documents/${encodeURIComponent(docId)}`,
      { method: 'DELETE' },
      'delete failed',
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
    setAdminResult('adminKbResult', `索引已重建，sections=${data.sections || 0}`);
    await loadAdminKb();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, '重建索引失敗'), true);
  }
}

async function rebuildAdminCollection(collection) {
  return adminJson(`/v1/${encodeURIComponent(collection)}/rebuild`, { method: 'POST' }, 'rebuild failed');
}

async function rebuildAllAdminKb() {
  const app = adminApp();
  if (!app || !window.confirm('Rebuild all knowledge-base indexes?')) {
    return;
  }
  const results = [];
  for (const collection of ADMIN_COLLECTIONS) {
    try {
      const data = await rebuildAdminCollection(collection);
      results.push(`${collection}:${data.sections || 0}`);
    } catch (_) {
      results.push(`${collection}:ERR`);
    }
  }
  setAdminResult('adminKbResult', `Rebuild complete: ${results.join(', ')}`);
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
    const data = await adminJson('/work-orders/import-excel', { method: 'POST', body: formData }, 'import failed');
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
  try {
    await adminJson(`/mock-users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }, 'user update failed');
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
  const password = window.prompt(`New password for ${userId}`, 'demo1234');
  if (!password) {
    return;
  }
  try {
    await adminJson(`/mock-users/${encodeURIComponent(userId)}/password`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }, 'password reset failed');
    setAdminResult('adminKbResult', `Password reset for ${userId}; active sessions revoked`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'Password reset failed'), true);
  }
}

async function revokeAdminUserSessions(userId) {
  const app = adminApp();
  if (!app || !window.confirm(`Revoke all active sessions for ${userId}?`)) {
    return;
  }
  try {
    const data = await adminJson(`/mock-users/${encodeURIComponent(userId)}/sessions`, {
      method: 'DELETE',
    }, 'user session revoke failed');
    setAdminResult('adminKbResult', `Revoked ${data.revoked || 0} session(s) for ${userId}`);
    await loadAdminSessions();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'User session revoke failed'), true);
  }
}

async function createAdminUser() {
  const app = adminApp();
  if (!app) {
    return;
  }
  const userId = app.$('adminNewUserId')?.value.trim() || '';
  if (!userId) {
    setAdminResult('adminKbResult', 'user_id is required', true);
    return;
  }
  try {
    await adminJson('/mock-users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newAdminUserPayload(userId)),
    }, 'create user failed');
    clearAdminFields(ADMIN_NEW_USER_FIELDS);
    setAdminResult('adminKbResult', `User ${userId} created`);
    await loadAdminConsole();
  } catch (error) {
    setAdminResult('adminKbResult', app.formatError(error, 'User creation failed'), true);
  }
}

async function loadAdminConsole() {
  const app = adminApp();
  if (!app) {
    return;
  }
  try {
    const [usersData, ingestLog, settings, feedbackStats, workStats] = await Promise.all([
      app.apiJson('/mock-users'),
      app.apiJson('/ingest-log').catch(() => ({ entries: [] })),
      loadAdminSettings(),
      app.apiJson('/feedback/stats').catch(() => ({ total: 0, rate: '0%' })),
      app.apiJson('/work-orders/stats').catch(() => ({ open_orders: 0, overdue_open: 0 })),
    ]);
    const users = usersData.users || [];
    const entries = ingestLog.entries || [];
    app.patchState({ adminUsers: users, adminIngestEntries: entries, adminSettings: settings });
    renderAdminOpsSummary(users, entries, settings, feedbackStats, workStats);
    renderAdminUsers(users);
    renderAdminImportLog(entries);
    renderAdminAudit(entries, settings);
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
  ['adminRoleFilter', 'adminActiveFilter', 'adminUserSearch'].forEach((id) => {
    app.$(id)?.addEventListener('input', () => renderAdminUsers(app.getState('adminUsers') || []));
    app.$(id)?.addEventListener('change', () => renderAdminUsers(app.getState('adminUsers') || []));
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
