function getApp() {
  return window.AlarmApp || null;
}

const WORK_ORDER_COLUMNS = [
  { key: 'pending', title: '待處理' },
  { key: 'assigned', title: '已指派' },
  { key: 'in_progress', title: '處理中' },
  { key: 'completed', title: '已完成' },
  { key: 'verified', title: '已驗證' },
];

const WORK_ORDER_STATUS_LABELS = {
  pending: '待處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '已完成',
  verified: '已驗證',
};

const WORK_ORDER_PRIORITY_LABELS = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '緊急',
};

function formatDateTime(value) {
  if (!value) {
    return '—';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }

  return date.toLocaleString('zh-TW', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function setKbResult(message, isError = false) {
  const app = getApp();
  if (!app) {
    return;
  }

  const result = app.$('kbDocResult');
  if (!result) {
    return;
  }

  app.setResultMessage(result, `upload-result show${isError ? ' error' : ''}`, message);
}

function selectKB(btn) {
  const app = getApp();
  if (!app) {
    return;
  }

  document.querySelectorAll('#panel-kb .manual-btn').forEach((node) => node.classList.remove('active'));
  btn.classList.add('active');
  app.setState('kbCollection', btn.dataset.kb);
  loadKBStats();
  loadCollectionDocuments();
  loadIngestLog();
}

async function handlePdfUpload(input) {
  const app = getApp();
  if (!app) {
    return;
  }

  const file = input.files[0];
  if (!file) {
    return;
  }

  const progress = app.$('uploadProgress');
  const bar = app.$('uploadBar');
  const status = app.$('uploadStatus');
  const result = app.$('uploadResult');

  progress.classList.add('show');
  result.classList.remove('show');
  status.textContent = `上傳中：${file.name}`;
  bar.style.width = '30%';

  const formData = new FormData();
  formData.append('file', file);

  try {
    bar.style.width = '65%';
    status.textContent = '正在建立索引...';
    const data = await app.apiJson(`/v1/${app.getState('kbCollection')}/ingest`, {
      method: 'POST',
      body: formData,
    });

    if (data.status !== 'ok') {
      throw new Error(data.message || 'PDF 上傳失敗');
    }

    bar.style.width = '100%';
    app.setResultMessage(
      result,
      'upload-result show',
      `<strong>${app.esc(data.filename)}</strong> 已寫入 <strong>${app.esc(app.getState('kbCollection').toUpperCase())}</strong><br>
新增警報片段：${data.alarms_added}<br>
新增一般片段：${data.general_added}<br>
集合總片段數：${data.total_in_collection}`,
      true,
    );
    loadIngestLog();
    loadKBStats();
    loadCollectionDocuments();
  } catch (error) {
    app.setResultMessage(result, 'upload-result show error', app.formatError(error, 'PDF 上傳失敗'));
  } finally {
    window.setTimeout(() => {
      progress.classList.remove('show');
      bar.style.width = '0%';
    }, 1200);
    input.value = '';
  }
}

async function handleTextIngest() {
  const app = getApp();
  if (!app) {
    return;
  }

  const payload = {
    text: app.$('ingestText').value.trim(),
    code: app.$('ingestCode').value.trim(),
    title: app.$('ingestTitle').value.trim(),
    source: app.$('ingestSource').value,
  };

  if (!payload.text) {
    window.alert('請先輸入要寫入知識庫的內容');
    return;
  }

  const result = app.$('textResult');
  const button = app.$('ingestBtn');
  button.disabled = true;
  result.classList.remove('show');

  try {
    const data = await app.apiJson(`/v1/${app.getState('kbCollection')}/ingest-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (data.status !== 'ok') {
      throw new Error(data.message || '文字寫入失敗');
    }

    app.setResultMessage(
      result,
      'upload-result show',
      `已寫入 <strong>${app.esc(app.getState('kbCollection').toUpperCase())}</strong>，目前總片段數：${data.total_in_collection}`,
      true,
    );
    app.$('ingestText').value = '';
    app.$('ingestCode').value = '';
    app.$('ingestTitle').value = '';
    loadIngestLog();
    loadKBStats();
    loadCollectionDocuments();
  } catch (error) {
    app.setResultMessage(result, 'upload-result show error', app.formatError(error, '文字寫入失敗'));
  } finally {
    button.disabled = false;
  }
}

async function loadIngestLog() {
  const app = getApp();
  if (!app) {
    return;
  }

  const list = app.$('ingestLogList');
  if (!list) {
    return;
  }

  try {
    const data = await app.apiJson('/ingest-log');
    const activeCollection = app.getState('kbCollection');
    const entries = (data.entries || [])
      .filter((entry) => !activeCollection || !entry.collection || entry.collection === activeCollection)
      .slice()
      .reverse();

    if (!entries.length) {
      list.innerHTML = '<div class="log-empty">尚無寫入紀錄</div>';
      return;
    }

    list.innerHTML = entries.map((entry) => {
      const time = formatDateTime(entry.time);
      const name = entry.filename || entry.title || entry.doc_id || '未命名項目';
      const itemType = String(entry.type || entry.action || 'event').toUpperCase();
      const count = entry.total ?? entry.removed_sections ?? 0;
      return `<div class="ingest-log-item">
        <span class="il-time">${app.esc(time)}</span>
        <span class="il-name">${app.esc(name)}</span>
        <span class="il-type ${app.esc(String(entry.type || 'text'))}">${app.esc(itemType)}</span>
        <span class="il-count">${entry.action === 'delete' ? '-' : '+'}${app.esc(String(count))}</span>
      </div>`;
    }).join('');
  } catch (_) {
    list.innerHTML = '<div class="log-empty">讀取寫入紀錄失敗</div>';
  }
}

async function loadKBStats() {
  const app = getApp();
  if (!app) {
    return;
  }

  const el = app.$('kbStats');
  if (!el) {
    return;
  }

  try {
    const [health, collectionsData] = await Promise.all([
      app.apiJson('/health'),
      app.apiJson('/collections'),
    ]);
    const summaries = (collectionsData.collections || []).reduce((map, item) => {
      map[item.name] = item;
      return map;
    }, {});
    const names = new Set([
      ...Object.keys(health.collections || {}),
      ...Object.keys(summaries),
    ]);

    if (!names.size) {
      el.textContent = '目前沒有可用的知識庫集合';
      return;
    }

    el.innerHTML = [...names].sort().map((name) => {
      const healthInfo = health.collections?.[name] || {};
      const summary = summaries[name] || {};
      const sections = summary.sections ?? healthInfo.alarms_indexed ?? 0;
      const documents = summary.documents ?? 0;
      const ready = summary.ready ?? healthInfo.ready ?? false;
      const updatedAt = summary.updated_at ? formatDateTime(summary.updated_at) : '—';
      const note = summary.has_legacy_index ? '舊索引' : `${documents} 份文件`;
      return `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <span style="font-family:var(--mono);font-weight:700;color:var(--acc);min-width:64px">${app.esc(name.toUpperCase())}</span>
          <span style="font-family:var(--mono);font-weight:700;font-size:18px">${sections}</span>
          <span style="color:var(--dim)">片段</span>
          <span style="font-size:11px;color:var(--dim)">${app.esc(note)}</span>
          <span style="margin-left:auto;font-size:11px;color:${ready ? 'var(--grn)' : 'var(--red)'}">${ready ? 'READY' : 'NOT READY'}</span>
        </div>
        <div style="font-size:11px;color:var(--dim);margin:-2px 0 10px 74px">最近更新：${app.esc(updatedAt)}</div>
      `;
    }).join('');
  } catch (_) {
    el.textContent = '讀取知識庫統計失敗';
  }
}

function renderCollectionDocuments() {
  const app = getApp();
  if (!app) {
    return;
  }

  const summaryEl = app.$('kbCollectionSummary');
  const listEl = app.$('kbDocumentList');
  if (!summaryEl || !listEl) {
    return;
  }

  const summary = app.getState('kbCollectionSummary');
  const documents = app.getState('kbDocuments') || [];

  if (summary) {
    const statusClass = summary.ready ? 'ready' : 'not-ready';
    summaryEl.innerHTML = `
      <div class="kb-summary-card">
        <div class="label">文件數</div>
        <div class="value">${app.esc(String(summary.documents ?? 0))}</div>
      </div>
      <div class="kb-summary-card">
        <div class="label">片段數</div>
        <div class="value">${app.esc(String(summary.sections ?? 0))}</div>
      </div>
      <div class="kb-summary-card">
        <div class="label">狀態</div>
        <div class="value ${statusClass}">${summary.ready ? 'READY' : 'NOT READY'}</div>
      </div>
    `;
  }

  if (!documents.length) {
    listEl.innerHTML = '<div class="log-empty">此集合目前沒有文件紀錄</div>';
    return;
  }

  listEl.innerHTML = documents.map((doc) => {
    const kind = String(doc.kind || 'text').toLowerCase();
    const importedAt = formatDateTime(doc.imported_at);
    const deleteButton = doc.legacy
      ? ''
      : `<button class="wo-btn danger" onclick="deleteKnowledgeDocument(${app.toJsArg(doc.doc_id)})">刪除</button>`;
    return `<div class="kb-doc-item">
      <div class="kb-doc-main">
        <div class="kb-doc-title">${app.esc(doc.filename || doc.doc_id || '未命名文件')}</div>
        <div class="kb-doc-meta">
          <span class="kb-doc-badge kind-${app.esc(kind)}">${app.esc(kind.toUpperCase())}</span>
          <span class="kb-doc-badge">${app.esc(String(doc.sections || 0))} 片段</span>
          <span class="kb-doc-badge">v${app.esc(String(doc.version ?? 1))}</span>
          ${doc.legacy ? '<span class="kb-doc-badge kind-legacy">LEGACY INDEX</span>' : ''}
        </div>
        <div class="kb-doc-sub">
          文件 ID：${app.esc(doc.doc_id || '—')}<br>
          匯入時間：${app.esc(importedAt)}
        </div>
      </div>
      <div class="kb-doc-actions">
        ${deleteButton}
      </div>
    </div>`;
  }).join('');
}

async function loadCollectionDocuments() {
  const app = getApp();
  if (!app) {
    return;
  }

  const listEl = app.$('kbDocumentList');
  if (!listEl) {
    return;
  }

  listEl.innerHTML = '<div class="log-empty">載入中...</div>';
  try {
    const data = await app.apiJson(`/v1/${app.getState('kbCollection')}/documents`);
    app.patchState({
      kbDocuments: data.documents || [],
      kbCollectionSummary: data.summary || null,
    });
    renderCollectionDocuments();
  } catch (error) {
    app.patchState({
      kbDocuments: [],
      kbCollectionSummary: null,
    });
    listEl.innerHTML = `<div class="log-empty">${app.esc(app.formatError(error, '讀取文件清單失敗'))}</div>`;
  }
}

async function rebuildKnowledgeBase() {
  const app = getApp();
  if (!app) {
    return;
  }

  const collection = app.getState('kbCollection');
  if (!window.confirm(`要重建 ${collection.toUpperCase()} 的索引嗎？`)) {
    return;
  }

  setKbResult(`正在重建 ${app.esc(collection.toUpperCase())} 索引...`);
  try {
    const data = await app.apiJson(`/v1/${collection}/rebuild`, { method: 'POST' });
    if (data.status !== 'ok') {
      throw new Error(data.message || '索引重建失敗');
    }
    setKbResult(`索引重建完成，已重建 ${app.esc(String(data.sections || 0))} 個片段。`);
    loadKBStats();
    loadCollectionDocuments();
  } catch (error) {
    setKbResult(app.formatError(error, '索引重建失敗'), true);
  }
}

async function deleteKnowledgeDocument(docId) {
  const app = getApp();
  if (!app) {
    return;
  }

  const collection = app.getState('kbCollection');
  if (!window.confirm(`要刪除文件 ${docId} 嗎？此操作會同步重建集合索引。`)) {
    return;
  }

  setKbResult(`正在刪除文件 ${app.esc(docId)}...`);
  try {
    const data = await app.apiJson(`/v1/${collection}/documents/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    });
    if (data.status !== 'ok') {
      throw new Error(data.message || '文件刪除失敗');
    }
    setKbResult(`文件已刪除，移除 ${app.esc(String(data.removed_sections || 0))} 個片段。`);
    loadIngestLog();
    loadKBStats();
    loadCollectionDocuments();
  } catch (error) {
    setKbResult(app.formatError(error, '文件刪除失敗'), true);
  }
}

function setSettingsResult(message, isError = false) {
  const app = getApp();
  const result = app?.$('settingsResult');
  if (!app || !result) {
    return;
  }
  app.setResultMessage(result, `upload-result show${isError ? ' error' : ''}`, message);
}

function renderSystemSettings(settings) {
  const app = getApp();
  if (!app || !settings) {
    return;
  }
  app.$('settingsDefaultManual').value = settings.default_manual || '808d';
  app.$('settingsSessionHours').value = String(settings.session_hours || 12);
  app.$('settingsAllowOperatorReopen').checked = Boolean(settings.allow_operator_reopen);
  const updatedBy = settings.updated_by ? `，最後更新：${settings.updated_by}` : '';
  setSettingsResult(`設定已載入${updatedBy}`);
}

async function loadSystemSettings() {
  const app = getApp();
  if (!app || !app.$('settingsResult')) {
    return;
  }
  try {
    const data = await app.apiJson('/system-settings');
    if (data.status !== 'ok') {
      throw new Error(data.message || '設定載入失敗');
    }
    renderSystemSettings(data.settings);
  } catch (error) {
    setSettingsResult(app.formatError(error, '設定載入失敗'), true);
  }
}

async function saveSystemSettings() {
  const app = getApp();
  if (!app) {
    return;
  }
  const payload = {
    default_manual: app.$('settingsDefaultManual').value,
    session_hours: Number(app.$('settingsSessionHours').value || 12),
    allow_operator_reopen: app.$('settingsAllowOperatorReopen').checked,
  };
  try {
    const data = await app.apiJson('/system-settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.status !== 'ok') {
      throw new Error(data.message || '設定儲存失敗');
    }
    renderSystemSettings(data.settings);
    setSettingsResult('設定已儲存');
  } catch (error) {
    setSettingsResult(app.formatError(error, '設定儲存失敗'), true);
  }
}

async function loadWorkOrders() {
  const app = getApp();
  if (!app || !app.hasElements(['woBoard', 'woTotal', 'woDoneToday', 'woAvg', 'woToday'])) {
    return;
  }

  try {
    const [list, stats] = await Promise.all([
      app.apiPaged('/work-orders/page', 'orders'),
      app.apiJson('/work-orders/stats'),
    ]);
    app.patchState({
      workOrders: list.orders || [],
      workOrderStats: stats,
    });
    renderWorkOrders();
  } catch (error) {
    console.error('loadWorkOrders', error);
  }
}

function renderWorkOrders() {
  const app = getApp();
  if (!app) {
    return;
  }

  const board = app.$('woBoard');
  if (!board) {
    return;
  }

  const workOrders = app.getState('workOrders') || [];
  const stats = app.getState('workOrderStats');

  board.innerHTML = WORK_ORDER_COLUMNS.filter((column) => column.key !== 'verified').map((column) => {
    const items = workOrders.filter((order) => order.status === column.key);
    const cards = items.length ? items.map(woCard).join('') : '<div class="wo-empty">目前沒有工單</div>';
    return `<div class="wo-col">
      <div class="wo-col-head">
        <span class="wo-col-title">${column.title}</span>
        <span class="wo-col-count">${items.length}</span>
      </div>
      ${cards}
    </div>`;
  }).join('');

  if (stats) {
    app.$('woTotal').textContent = String(stats.total ?? '--');
    app.$('woDoneToday').textContent = String(stats.today_completed ?? '--');
    app.$('woToday').textContent = String(stats.today_created ?? '--');
    app.$('woAvg').textContent = String(stats.avg_hours ?? '--');
  }
}

function woCard(order) {
  const app = getApp();
  if (!app) {
    return '';
  }

  const description = order.description ? order.description.slice(0, 90) : '尚無描述';
  return `<div class="wo-card" onclick="openWoModal(${app.toJsArg(order.id)})">
    <div class="wo-code">#${app.esc(order.id)} · Alarm ${app.esc(order.alarm_code)}</div>
    <div class="wo-desc">${app.esc(description)}</div>
    <div class="wo-meta">
      <span class="wo-badge prio-${app.esc(order.priority)}">優先 ${app.esc(WORK_ORDER_PRIORITY_LABELS[order.priority] || order.priority)}</span>
      <span class="wo-badge status-pill-sm">${app.esc(WORK_ORDER_STATUS_LABELS[order.status] || order.status)}</span>
      <span class="wo-badge">設備 ${app.esc(order.machine_id || 'N/A')}</span>
    </div>
  </div>`;
}

async function createWorkOrder() {
  const app = getApp();
  if (!app) {
    return;
  }

  const alarm = app.$('woAlarm').value.trim();
  if (!alarm) {
    window.alert('請輸入警報代碼');
    return;
  }

  const payload = {
    alarm_code: alarm,
    manual: app.$('woManual').value,
    machine_id: app.$('woMachine').value.trim(),
    priority: app.$('woPriority').value,
    assigned_to: app.$('woAssignee').value.trim(),
    description: app.$('woDesc').value.trim(),
    rag_suggestion: '',
    source: app.$('woSource').value.trim() || 'manual',
  };

  try {
    const data = await app.apiJson('/work-orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.status !== 'ok') {
      throw new Error(data.message || '新增工單失敗');
    }

    ['woAlarm', 'woMachine', 'woAssignee', 'woSource', 'woDesc'].forEach((id) => {
      app.$(id).value = '';
    });
    app.$('woPriority').value = 'medium';
    app.$('woManual').value = '808d';
    loadWorkOrders();
  } catch (error) {
    window.alert(app.formatError(error, '新增工單失敗'));
  }
}

function openWoModal(id) {
  const app = getApp();
  if (!app) {
    return;
  }

  const order = (app.getState('workOrders') || []).find((item) => item.id === id);
  if (!order) {
    return;
  }

  app.setState('woCurrentId', id);
  app.$('woModalTitle').textContent = `Alarm ${order.alarm_code}`;
  app.$('woModalId').textContent = `ID: ${order.id} · ${String(order.manual).toUpperCase()}`;
  app.$('woEditStatus').value = order.status;
  app.$('woEditPriority').value = order.priority;
  app.$('woEditAssignee').value = order.assigned_to || '';
  app.$('woEditMachine').value = order.machine_id || '';
  app.$('woEditDesc').value = order.description || '';
  app.$('woEditResolution').value = order.resolution || '';
  app.$('woEditNotes').value = order.notes || '';
  if (app.$('woEditAcceptedBy')) app.$('woEditAcceptedBy').value = order.accepted_by || '';
  if (app.$('woEditCompletedBy')) app.$('woEditCompletedBy').value = order.completed_by || '';
  if (app.$('woEditRootCause')) app.$('woEditRootCause').value = order.root_cause || '';
  if (app.$('woEditFailureCategory')) app.$('woEditFailureCategory').value = order.failure_category || '';
  if (app.$('woEditLlmCorrectness')) app.$('woEditLlmCorrectness').value = order.llm_correctness || '';
  if (app.$('woEditLlmCoverage')) app.$('woEditLlmCoverage').value = order.llm_coverage || '';
  if (app.$('woEditRepairAction')) app.$('woEditRepairAction').value = order.repair_action || '';
  if (app.$('woEditLlmMissingInfo')) app.$('woEditLlmMissingInfo').value = order.llm_missing_info || '';
  app.$('woModal').classList.add('show');
}

function closeWoModal() {
  const app = getApp();
  if (!app) {
    return;
  }

  app.$('woModal').classList.remove('show');
  app.setState('woCurrentId', null);
}

async function saveWorkOrder() {
  const app = getApp();
  if (!app) {
    return;
  }

  const currentId = app.getState('woCurrentId');
  if (!currentId) {
    return;
  }

  const payload = {
    status: app.$('woEditStatus').value,
    priority: app.$('woEditPriority').value,
    assigned_to: app.$('woEditAssignee').value.trim(),
    machine_id: app.$('woEditMachine').value.trim(),
    description: app.$('woEditDesc').value.trim(),
    resolution: app.$('woEditResolution').value.trim(),
    notes: app.$('woEditNotes').value.trim(),
    accepted_by: app.$('woEditAcceptedBy')?.value.trim() || '',
    completed_by: app.$('woEditCompletedBy')?.value.trim() || '',
    root_cause: app.$('woEditRootCause')?.value.trim() || '',
    repair_action: app.$('woEditRepairAction')?.value.trim() || '',
    failure_category: app.$('woEditFailureCategory')?.value.trim() || '',
    llm_correctness: app.$('woEditLlmCorrectness')?.value || '',
    llm_coverage: app.$('woEditLlmCoverage')?.value || '',
    llm_missing_info: app.$('woEditLlmMissingInfo')?.value.trim() || '',
    llm_expected_fix: app.$('woEditResolution').value.trim(),
    llm_answer_used: Boolean(app.$('woEditLlmCorrectness')?.value || app.$('woEditLlmCoverage')?.value),
  };

  try {
    const data = await app.apiJson(`/work-orders/${currentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (data.status !== 'ok') {
      throw new Error(data.message || '更新工單失敗');
    }
    closeWoModal();
    loadWorkOrders();
    loadCollectionDocuments();
  } catch (error) {
    window.alert(app.formatError(error, '更新工單失敗'));
  }
}

async function deleteWorkOrder() {
  const app = getApp();
  if (!app) {
    return;
  }

  const currentId = app.getState('woCurrentId');
  if (!currentId) {
    return;
  }

  if (!window.confirm('確定要刪除此工單嗎？')) {
    return;
  }

  try {
    await app.apiJson(`/work-orders/${currentId}`, { method: 'DELETE' });
    closeWoModal();
    loadWorkOrders();
  } catch (error) {
    window.alert(app.formatError(error, '刪除工單失敗'));
  }
}

async function uploadExcel(input) {
  const app = getApp();
  if (!app) {
    return;
  }

  const file = input.files[0];
  if (!file) {
    return;
  }

  const result = app.$('excelResult');
  app.setResultMessage(result, 'excel-result show', `上傳中：${file.name}`);
  result.style.background = 'var(--acc-lt)';
  result.style.color = 'var(--acc)';
  result.style.border = '1px solid #bfdbfe';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const data = await app.apiJson('/work-orders/import-excel', {
      method: 'POST',
      body: formData,
    });
    if (data.status !== 'ok') {
      throw new Error(data.message || 'Excel 匯入失敗');
    }

    let message = `匯入完成：${data.filename}\n`;
    message += `成功匯入：${data.imported} 筆\n`;
    message += `略過：${data.skipped} 筆\n`;
    if (data.candidate_count) {
      message += `待審核候選知識：${data.candidate_count} 筆\n`;
    }
    if (data.errors?.length) {
      message += `錯誤：${data.errors.join(', ')}\n`;
    }
    app.setResultMessage(result, 'excel-result show ok', message);
    result.style.background = '';
    result.style.color = '';
    result.style.border = '';
    loadWorkOrders();
    loadCollectionDocuments();
  } catch (error) {
    app.setResultMessage(result, 'excel-result show err', app.formatError(error, 'Excel 匯入失敗'));
    result.style.background = '';
    result.style.color = '';
    result.style.border = '';
  } finally {
    input.value = '';
  }
}

const operationsApp = getApp();
if (operationsApp) {
  Object.assign(operationsApp, {
    selectKB,
    handlePdfUpload,
    handleTextIngest,
    loadIngestLog,
    loadKBStats,
    loadCollectionDocuments,
    renderCollectionDocuments,
    rebuildKnowledgeBase,
    deleteKnowledgeDocument,
    loadSystemSettings,
    saveSystemSettings,
    loadWorkOrders,
    renderWorkOrders,
    woCard,
    createWorkOrder,
    openWoModal,
    closeWoModal,
    saveWorkOrder,
    deleteWorkOrder,
    uploadExcel,
  });
}
