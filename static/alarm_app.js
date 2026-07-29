const coreApi = window.AlarmCoreApi || {};
const coreStorage = window.AlarmCoreStorage || {};
const RAG_BASE = coreApi.baseUrl || window.location.origin;
const STORAGE_KEYS = coreStorage.keys || {
  alarmHistory: 'alarmHistory',
  alarmLog: 'alarmLog',
  fbBad: 'fbBad',
  fbGood: 'fbGood',
  fbHistory: 'fbHistory',
  queryTimings: 'queryTimings',
};
const LOOKUP_FIELDS = [
  { key: 'Parameters', label: '參數', cls: 'fl-params', icon: '⚙️' },
  { key: 'Explanation', label: '說明', cls: 'fl-expl', icon: '📘' },
  { key: 'Reaction', label: '系統反應', cls: 'fl-react', icon: '⚡' },
  { key: 'Remedy', label: '處置方式', cls: 'fl-remedy', icon: '🔧' },
  { key: 'Program continuation', label: '程式續行', cls: 'fl-prog', icon: '▶️' },
];
const LOOKUP_SECTIONS = 'Parameters|Explanation|Reaction|Remedy|Program continuation|Manual Page';
const EMPTY_CHAT_HTML = `
  <div class="chat-empty" id="chatEmpty">
    <div class="chat-empty-icon">🤖</div>
    <div class="chat-empty-text">歡迎使用智慧對話系統<br>可以用自然語言提問<br><span class="u-text-xs u-text-muted">支援多輪對話與知識庫查詢</span></div>
  </div>`;
const WO_STATUS_LABEL = {
  pending: '待處理',
  assigned: '已指派',
  in_progress: '處理中',
  completed: '已完成',
  verified: '已驗證',
};
const WO_PRIORITY_LABEL = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '緊急',
};

const appState = {
  currentUser: coreApi.readAuthUser ? coreApi.readAuthUser() : null,
  lookupManual: '808d',
  kbCollection: '808d',
  chatCollection: '808d',
  lastQuery: '',
  pendingCode: null,
  pendingManual: null,
  chatHistory: [],
  alarmLog: readStorage(STORAGE_KEYS.alarmLog, []),
  queryTimings: readStorage(STORAGE_KEYS.queryTimings, []),
  workOrders: [],
  workOrderStats: null,
  kbDocuments: [],
  kbCollectionSummary: null,
  woCurrentId: null,
  pageBindingsReady: false,
  alarmPollTimerId: null,
};
const PAGE_NAME = document.body?.dataset?.page || 'dashboard';
const PAGE_PATHS = {
  admin: '/admin',
  assistant: '/assistant',
  dashboard: '/dashboard',
  operator: '/operator',
  maintenance: '/maintenance',
  operations: '/operations',
  supervisor: '/supervisor',
};
const DECLARATIVE_ACTIONS = new Set([
  'AlarmApp.logout',
  'AnswerTrace.open',
  'acceptWorkOrder',
  'addOperatorIssueNote',
  'askInChat',
  'autoResize',
  'bannerLookup',
  'bulkUpdateSupervisorOrders',
  'chatKeydown',
  'chatSuggest',
  'clearChat',
  'clearLog',
  'closeMaintenanceModal',
  'closeOperatorIssueModal',
  'closeWoModal',
  'completeWorkOrder',
  'copyAlarmCode',
  'createAdminUser',
  'createOperatorIssue',
  'createWorkOrder',
  'createWorkOrderFromIssue',
  'deleteAdminKbDocument',
  'deleteKnowledgeDocument',
  'deleteMaintenanceWorkOrder',
  'deleteWorkOrder',
  'dismissBanner',
  'doSearch',
  'escalateOperatorIssue',
  'exportAdminAuditCsv',
  'exportAdminKbCsv',
  'exportAdminQualityCsv',
  'exportAdminUsersCsv',
  'exportSupervisorCsv',
  'filterRefTable',
  'handlePdfUpload',
  'handleTextIngest',
  'ingestAdminText',
  'loadAdminConsole',
  'loadAdminKb',
  'loadAdminSessions',
  'loadAdminSettings',
  'loadBI',
  'loadCollectionDocuments',
  'loadHist',
  'loadMaintenanceData',
  'loadOperatorIssues',
  'loadSupervisorConsole',
  'loadSystemSettings',
  'loadWorkOrders',
  'lookupAlarm',
  'lookupOperatorSuggestion',
  'openMaintenanceModal',
  'openOperatorIssueModal',
  'openWoModal',
  'rebuildAdminKb',
  'rebuildAllAdminKb',
  'rebuildKnowledgeBase',
  'reopenOperatorIssue',
  'requestSupervisorRework',
  'resetAdminPassword',
  'reviewAdminKnowledge',
  'revokeAdminSession',
  'revokeAdminUserSessions',
  'saveAdminSettings',
  'saveAdminUser',
  'saveMaintenanceWorkOrder',
  'saveMsgToKB',
  'saveOperatorIssueEdit',
  'saveSystemSettings',
  'saveWorkOrder',
  'selectAdminSection',
  'selectChatManual',
  'selectKB',
  'selectManual',
  'sendChat',
  'sendFeedback',
  'sendOperatorSuggestionFeedback',
  'startWorkOrder',
  'switchTab',
  'testTrigger',
  'toggleAdminUser',
  'toggleTestTools',
  'updateSupervisorOrder',
  'uploadAdminExcel',
  'uploadAdminPdf',
  'uploadExcel',
  'verifyOperatorIssue',
  'verifySupervisorOrder',
]);
const DECLARATIVE_EVENTS = ['click', 'change', 'input', 'keydown'];

function resolveDeclarativeAction(name) {
  if (!DECLARATIVE_ACTIONS.has(name)) {
    return null;
  }
  return name.split('.').reduce((value, key) => value?.[key], window);
}

function declarativeActionArgs(element, eventType) {
  const encoded = element.getAttribute(`data-${eventType}-args`) || element.dataset.actionArgs;
  if (!encoded) {
    return [];
  }
  try {
    const parsed = JSON.parse(encoded);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function handleDeclarativeEvent(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) {
    return;
  }
  const eventAttribute = `data-on-${event.type}`;
  const selector = event.type === 'click'
    ? `[${eventAttribute}], [data-nav], [data-role-home], [data-file-trigger]`
    : `[${eventAttribute}]`;
  const element = target.closest(selector);
  if (!element) {
    return;
  }

  if (event.type === 'click' && element.dataset.nav) {
    window.location.href = element.dataset.nav;
    return;
  }
  if (event.type === 'click' && element.hasAttribute('data-role-home')) {
    window.location.href = coreApi.roleHome?.(coreApi.readAuthUser?.()?.role) || '/login';
    return;
  }
  if (event.type === 'click' && element.dataset.fileTrigger) {
    document.getElementById(element.dataset.fileTrigger)?.click();
    return;
  }

  const action = resolveDeclarativeAction(element.getAttribute(eventAttribute) || '');
  if (typeof action !== 'function') {
    console.error(`Blocked or unavailable declarative action: ${element.getAttribute(eventAttribute) || ''}`);
    return;
  }
  if (element.hasAttribute('data-action-stop')) {
    event.stopPropagation();
  }
  if (element.hasAttribute('data-action-prevent')) {
    event.preventDefault();
  }
  const args = declarativeActionArgs(element, event.type);
  if (element.hasAttribute(`data-${event.type}-target`) || element.hasAttribute('data-action-target')) {
    args.push(element);
  }
  if (element.hasAttribute(`data-${event.type}-event`) || element.hasAttribute('data-action-event')) {
    args.push(event);
  }
  action(...args);
}

DECLARATIVE_EVENTS.forEach((eventName) => {
  document.addEventListener(eventName, handleDeclarativeEvent);
});

function getState(key) {
  if (!key) {
    return { ...appState };
  }
  return appState[key];
}

function setState(key, value) {
  appState[key] = value;
  return value;
}

function patchState(nextState) {
  Object.assign(appState, nextState);
  return { ...appState };
}

function $(id) {
  return document.getElementById(id);
}

function hasElements(ids) {
  return ids.every((id) => Boolean($(id)));
}

function navigateTo(page, params = {}) {
  const path = PAGE_PATHS[page];
  if (!path) {
    return;
  }
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, String(value));
    }
  });
  window.location.href = url.pathname + url.search;
}

function readStorage(key, fallback) {
  if (coreStorage.readStorage) {
    return coreStorage.readStorage(key, fallback);
  }
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeStorage(key, value) {
  if (coreStorage.writeStorage) {
    coreStorage.writeStorage(key, value);
    return;
  }
  localStorage.setItem(key, JSON.stringify(value));
}

function esc(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function toJsArg(value) {
  return JSON.stringify(String(value)).replace(/"/g, '&quot;');
}

function formatError(error, fallback) {
  return error?.message ? `${fallback}：${error.message}` : fallback;
}

async function parseJsonResponse(res) {
  if (coreApi.parseJsonResponse) {
    return coreApi.parseJsonResponse(res);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.message || `Server error: ${res.status}`);
  }
  if (data?.status === 'error') {
    if (data.message === 'Not authenticated') {
      coreApi.clearAuth?.();
      coreApi.requireAuth?.();
    }
    throw new Error(data.message || '請求失敗');
  }
  return data;
}

async function apiJson(path, options = {}) {
  if (coreApi.apiJson) {
    return coreApi.apiJson(path, options);
  }
  const res = await fetch(`${RAG_BASE}${path}`, options);
  return parseJsonResponse(res);
}

async function apiPaged(path, collectionKey, options = {}) {
  if (coreApi.apiPaged) {
    return coreApi.apiPaged(path, collectionKey, options);
  }
  return apiJson(path);
}

function currentUser() {
  return appState.currentUser || (coreApi.readAuthUser ? coreApi.readAuthUser() : null);
}

function currentUserId() {
  return currentUser()?.user_id || '';
}

function currentUserRole() {
  return currentUser()?.role || '';
}

function authLabel() {
  const user = currentUser();
  if (!user?.user_id) {
    return '';
  }
  return `${user.user_id} (${user.role || 'user'})`;
}

function applyAuthChrome() {
  const label = authLabel();
  if (!label) {
    return;
  }
  ['operatorUserLabel', 'maintenanceUserLabel', 'supervisorUserLabel', 'adminUserLabel', 'authUserLabel'].forEach((id) => {
    const node = $(id);
    if (node) {
      node.textContent = label;
    }
  });
  document.querySelectorAll('header .status-pill').forEach((pill) => {
    if (!pill.querySelector('#operatorUserLabel, #maintenanceUserLabel, #supervisorUserLabel, #adminUserLabel, #authUserLabel')) {
      const span = pill.querySelector('span');
      if (span) {
        span.textContent = label;
        span.id = 'authUserLabel';
      }
    }
    if (!pill.querySelector('.auth-logout')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'auth-logout';
      button.dataset.authLogout = 'true';
      button.textContent = '登出';
      button.addEventListener('click', logout);
      pill.appendChild(button);
    }
  });
}

async function logout() {
  const token = coreApi.readAuthToken ? coreApi.readAuthToken() : '';
  try {
    await apiJson('/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
  } catch (_) {
    // Local logout should complete even if the server session already expired.
  }
  coreApi.clearAuth?.();
  window.location.href = '/login';
}

function setResultMessage(element, className, message, asHtml = false) {
  element.className = className;
  if (asHtml) {
    element.innerHTML = message;
    return;
  }
  element.textContent = message;
}

function appendBars(values, maxValue, renderBar) {
  return values.map((item) => renderBar(item, maxValue)).join('');
}

function formatDuration(ms) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function sizeClass(kind, value, maxValue = 120, step = 5) {
  const numeric = Number(value);
  const safeValue = Number.isFinite(numeric) ? numeric : 0;
  const bounded = Math.min(Math.max(safeValue, 0), maxValue);
  const rounded = Math.round(bounded / step) * step;
  return `u-${kind}-${rounded}`;
}

function applySizeClass(element, kind, value, maxValue = 120, step = 5) {
  if (!element) {
    return;
  }
  const prefix = `u-${kind}-`;
  [...element.classList]
    .filter((className) => className.startsWith(prefix))
    .forEach((className) => element.classList.remove(className));
  element.classList.add(sizeClass(kind, value, maxValue, step));
}

function setTimerClass(el, ms) {
  el.classList.remove('fast', 'slow');
  if (ms < 2000) {
    el.classList.add('fast');
  } else if (ms > 6000) {
    el.classList.add('slow');
  }
}

function resetLookupPanels() {
  ['loading', 'resultCard', 'errorBox'].forEach((id) => $(id)?.classList.remove('show'));
}

function buildLookupFieldBlocks(getFieldValue) {
  let html = '';
  LOOKUP_FIELDS.forEach((field) => {
    const value = getFieldValue(field.key);
    if (!value) {
      return;
    }
    html += `<div class="field-block">
      <div class="field-label ${field.cls}">${field.icon} ${field.label}</div>
      <div class="field-content">${esc(value)}</div>
    </div>`;
  });
  return html;
}

function saveAlarmHistoryEntry(entry) {
  const current = readStorage(STORAGE_KEYS.alarmHistory, []);
  const next = [entry, ...current.filter((item) => !(item.code === entry.code && item.manual === entry.manual))].slice(0, 10);
  writeStorage(STORAGE_KEYS.alarmHistory, next);
  renderHistory();
}

function incrementStoredCounter(key) {
  if (coreStorage.incrementStoredCounter) {
    coreStorage.incrementStoredCounter(key);
    return;
  }
  const current = Number(localStorage.getItem(key) || '0');
  localStorage.setItem(key, String(current + 1));
}

function getTodayString() {
  return new Date().toLocaleDateString('zh-TW');
}

function buildRecentDays(days) {
  const items = [];
  for (let i = days - 1; i >= 0; i -= 1) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    items.push({
      date,
      dateKey: date.toLocaleDateString('zh-TW'),
      label: `${date.getMonth() + 1}/${date.getDate()}`,
    });
  }
  return items;
}

function wireDropZone(zoneId, inputId, matcher, onAccept) {
  const zone = $(zoneId);
  if (!zone) {
    return;
  }

  ['dragenter', 'dragover'].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove('drag-over');
    });
  });

  zone.addEventListener('drop', (event) => {
    const file = event.dataTransfer.files[0];
    if (!file || !matcher(file)) {
      return;
    }
    const input = $(inputId);
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    onAccept(input);
  });
}

function showTimer(retrievalMs, aiMs) {
  const total = retrievalMs + aiMs;
  [['timerRetrieval', retrievalMs], ['timerAI', aiMs], ['timerTotal', total]].forEach(([id, value]) => {
    const el = $(id);
    el.textContent = formatDuration(value);
    setTimerClass(el, value);
  });
  $('timerRow').classList.add('show');

  const nextTimings = [{ total, retrieval: retrievalMs, ai: aiMs, t: Date.now() }, ...appState.queryTimings].slice(0, 50);
  setState('queryTimings', nextTimings);
  writeStorage(STORAGE_KEYS.queryTimings, nextTimings);
}

async function loadBI() {
  if (!hasElements(['biAccuracy', 'biAccBar', 'biAccuracySub'])) {
    return;
  }
  let feedbackStats = null;
  let feedbackSource = 'local';
  try {
    feedbackStats = await apiJson('/feedback/stats');
    feedbackSource = 'server';
  } catch (_) {
    feedbackStats = null;
  }

  const good = feedbackStats?.good ?? Number(localStorage.getItem(STORAGE_KEYS.fbGood) || '0');
  const bad = feedbackStats?.bad ?? Number(localStorage.getItem(STORAGE_KEYS.fbBad) || '0');
  const total = good + bad;
  const accuracy = total > 0 ? Math.round((good / total) * 100) : null;

  const accEl = $('biAccuracy');
  const barEl = $('biAccBar');
  if (accuracy === null) {
    accEl.textContent = '—';
    accEl.className = 'bi-big';
    barEl.className = 'accuracy-bar-fill u-pct-0';
    $('biAccuracySub').textContent = '尚無回饋資料';
  } else {
    const stateClass = accuracy >= 80 ? 'grn' : accuracy >= 60 ? 'org' : 'red';
    accEl.textContent = `${accuracy}%`;
    accEl.className = `bi-big ${stateClass}`;
    barEl.className = `accuracy-bar-fill ${sizeClass('pct', accuracy, 100)}${accuracy >= 80 ? '' : accuracy >= 60 ? ' mid' : ' low'}`;
    $('biAccuracySub').textContent = `基於 ${total} 筆回饋 · ${feedbackSource === 'server' ? '後端統計' : '本地暫存'}`;
  }

  const arc = $('biDonutArc');
  const circumference = 2 * Math.PI * 28;
  const filled = total > 0 ? (good / total) * circumference : 0;
  arc.setAttribute('stroke-dasharray', `${filled.toFixed(1)} ${circumference.toFixed(1)}`);
  arc.setAttribute('stroke', accuracy >= 80 ? 'var(--grn)' : accuracy >= 60 ? 'var(--org)' : 'var(--red)');
  $('biGoodCount').textContent = `${good} 有幫助`;
  $('biBadCount').textContent = `${bad} 需改善`;
  $('biTotalFB').textContent = `${total} 總回饋`;

  const recentTimings = appState.queryTimings.slice(0, 10).reverse();
  const avgMs = recentTimings.length
    ? Math.round(recentTimings.reduce((sum, item) => sum + item.total, 0) / recentTimings.length)
    : null;
  $('biAvgTime').textContent = avgMs === null ? '—' : formatDuration(avgMs);
  const maxTiming = Math.max(...recentTimings.map((item) => item.total), 1);
  $('biSparkline').innerHTML = appendBars(recentTimings, maxTiming, (item, maxValue) => {
    const height = Math.max(4, Math.round((item.total / maxValue) * 34));
    const colorClass = item.total < 2000 ? 'u-spark-fast' : item.total > 6000 ? 'u-spark-slow' : 'u-spark-normal';
    return `<div class="spark-bar u-flex-1 ${sizeClass('h', height)} ${colorClass}" title="${item.total}ms"></div>`;
  });

  const today = getTodayString();
  const todayCount = appState.alarmLog.filter((item) => item.date === today).length;
  $('biTodayAlarms').textContent = String(todayCount);
  $('biTodayAlarmSub').textContent = todayCount === 0 ? '今日尚無警報' : `今日觸發 ${todayCount} 次`;

  const recentDays = buildRecentDays(7);
  const dailyAlarmCounts = recentDays.map((item) => ({
    label: item.label,
    cnt: appState.alarmLog.filter((entry) => entry.date === item.dateKey).length,
  }));
  const maxAlarmCount = Math.max(...dailyAlarmCounts.map((item) => item.cnt), 1);
  $('biAlarmChart').innerHTML = appendBars(dailyAlarmCounts, maxAlarmCount, (item, maxValue) => {
    const height = Math.max(3, Math.round((item.cnt / maxValue) * 55));
    const isToday = item.label === `${new Date().getMonth() + 1}/${new Date().getDate()}`;
    return `<div class="bar-col">
      <div class="bar-fill u-full-width ${sizeClass('h', height)} ${isToday ? 'org' : ''}"></div>
      <div class="bar-lbl">${item.label}</div>
    </div>`;
  });

  const manuals = [
    { key: '808d', label: '808D' },
    { key: '840d', label: '840D / 810D' },
    { key: '840dsl', label: '840D sl' },
    { key: 'furnace_b85t', label: 'FURNACE B85T' },
  ];
  const totalLogs = manuals.reduce((sum, manual) => sum + appState.alarmLog.filter((item) => item.manual === manual.key).length, 0) || 1;
  $('biManualBody').innerHTML = manuals.map((manual) => {
    const count = appState.alarmLog.filter((item) => item.manual === manual.key).length;
    const pct = Math.round((count / totalLogs) * 100);
    return `<tr>
      <td><strong class="u-mono">${manual.label}</strong></td>
      <td>${count}</td>
      <td>${pct}%</td>
      <td><span class="mini-bar ${sizeClass('w', Math.max(4, pct * 1.2))}"></span></td>
    </tr>`;
  }).join('');

  const feedbackHistory = readStorage(STORAGE_KEYS.fbHistory, []);
  const dailyFeedback = recentDays.map((item) => {
    const entries = feedbackHistory.filter((entry) => entry.date === item.dateKey);
    return {
      label: item.label,
      good: entries.filter((entry) => entry.type === 'good').length,
      bad: entries.filter((entry) => entry.type === 'bad').length,
    };
  });
  const maxFeedback = Math.max(...dailyFeedback.map((item) => item.good + item.bad), 1);
  $('biFeedbackChart').innerHTML = appendBars(dailyFeedback, maxFeedback, (item, maxValue) => {
    const goodHeight = Math.max(0, Math.round((item.good / maxValue) * 70));
    const badHeight = Math.max(0, Math.round((item.bad / maxValue) * 70));
    return `<div class="bar-col u-gap-1">
      <div class="bar-fill grn u-full-width ${sizeClass('h', goodHeight)}"></div>
      <div class="bar-fill red u-full-width u-rounded-bottom ${sizeClass('h', badHeight)}"></div>
      <div class="bar-lbl">${item.label}</div>
    </div>`;
  });
}

function switchTab(name, btn) {
  const targetPanel = $(`panel-${name}`);
  if (!targetPanel) {
    return;
  }

  document.querySelectorAll('.panel').forEach((node) => node.classList.remove('active'));
  const tabButtons = btn?.closest('.tabs')?.querySelectorAll('.tab-btn')
    || document.querySelectorAll('.tab-btn[data-tab]');
  tabButtons.forEach((node) => node.classList.remove('active'));
  targetPanel.classList.add('active');
  if (btn) {
    btn.classList.add('active');
  } else {
    document.querySelector(`.tab-btn[data-tab="${name}"]`)?.classList.add('active');
  }

  if (name === 'warning') {
    renderLog();
  }
  if (name === 'work') {
    loadWorkOrders();
  }
  if (name === 'bi') {
    loadBI();
  }
  if (name === 'chat') {
    $('chatInput')?.focus();
  }
  if (name === 'kb') {
    loadIngestLog();
    loadKBStats();
    loadCollectionDocuments();
  }
}

function calendarMonthKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  const safeDate = Number.isNaN(date.getTime()) ? new Date() : date;
  return `${safeDate.getFullYear()}-${String(safeDate.getMonth() + 1).padStart(2, '0')}`;
}

function shiftCalendarMonth(monthKey, offset) {
  const [year, month] = String(monthKey || calendarMonthKey()).split('-').map(Number);
  const date = new Date(year || new Date().getFullYear(), (month || 1) - 1 + offset, 1);
  return calendarMonthKey(date);
}

function calendarDateKey(value) {
  return String(value || '').slice(0, 10);
}

function renderResolvedCalendar({ calendarEl, listEl, items, monthKey, selectedDate, renderItem, emptyText }) {
  if (!calendarEl || !listEl) {
    return;
  }

  const currentMonth = monthKey || calendarMonthKey();
  const [year, month] = currentMonth.split('-').map(Number);
  const firstDay = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const leadingDays = firstDay.getDay();
  const byDate = (items || []).reduce((acc, item) => {
    const dateKey = calendarDateKey(item.date);
    if (!dateKey) {
      return acc;
    }
    acc[dateKey] = acc[dateKey] || [];
    acc[dateKey].push(item);
    return acc;
  }, {});
  const monthItems = Object.keys(byDate).filter((dateKey) => dateKey.startsWith(currentMonth)).sort();
  const todayKey = calendarDateKey(new Date().toISOString());
  const activeDate = selectedDate?.startsWith(currentMonth)
    ? selectedDate
    : monthItems[0] || (todayKey.startsWith(currentMonth) ? todayKey : `${currentMonth}-01`);
  const weekdayLabels = ['日', '一', '二', '三', '四', '五', '六'];
  const cells = [
    ...Array.from({ length: leadingDays }, () => '<div class="resolved-day muted"></div>'),
    ...Array.from({ length: daysInMonth }, (_, index) => {
      const day = index + 1;
      const dateKey = `${currentMonth}-${String(day).padStart(2, '0')}`;
      const count = byDate[dateKey]?.length || 0;
      const classes = [
        'resolved-day',
        count ? 'has-items' : '',
        dateKey === activeDate ? 'selected' : '',
        dateKey === todayKey ? 'today' : '',
      ].filter(Boolean).join(' ');
      return `<button class="${classes}" type="button" data-calendar-date="${esc(dateKey)}">
        <span>${day}</span>
        ${count ? `<b>${count}</b>` : ''}
      </button>`;
    }),
  ];

  calendarEl.innerHTML = `<div class="resolved-calendar">
    <div class="resolved-calendar-head">
      <button class="wo-btn alt" type="button" data-calendar-action="prev">上一月</button>
      <div>
        <div class="resolved-calendar-title">${year} 年 ${month} 月</div>
        <div class="resolved-calendar-sub">${monthItems.length} 天有已解決紀錄</div>
      </div>
      <button class="wo-btn alt" type="button" data-calendar-action="next">下一月</button>
    </div>
    <div class="resolved-weekdays">${weekdayLabels.map((label) => `<span>${label}</span>`).join('')}</div>
    <div class="resolved-grid">${cells.join('')}</div>
  </div>`;

  const activeItems = byDate[activeDate] || [];
  listEl.innerHTML = `<div class="resolved-list-head">
    <div class="resolved-list-title">${esc(activeDate)}</div>
    <div class="resolved-list-count">${activeItems.length} 筆</div>
  </div>
  ${activeItems.length
    ? `<div class="resolved-list">${activeItems.map(renderItem).join('')}</div>`
    : `<div class="maintenance-empty-state"><div class="maintenance-empty-title">這天沒有已解決紀錄</div><div class="maintenance-empty-text">${esc(emptyText || '請切換到有標記的日期。')}</div></div>`}`;
}

function initCommonPageBindings() {
  if (appState.pageBindingsReady) {
    return;
  }
  setState('pageBindingsReady', true);

  $('searchInput')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      doSearch();
    }
  });
  $('woModal')?.addEventListener('click', (event) => {
    if (event.target === $('woModal')) {
      closeWoModal();
    }
  });

  if (typeof handlePdfUpload === 'function') {
    wireDropZone('uploadZone', 'pdfFile', (file) => file.name.toLowerCase().endsWith('.pdf'), handlePdfUpload);
  }
  if (typeof uploadExcel === 'function') {
    wireDropZone('excelZone', 'excelFile', (file) => /\.xlsx?$/i.test(file.name), uploadExcel);
  }
  startAlarmPolling();
}

function startAlarmPolling() {
  if (typeof pollAlarms !== 'function') {
    return;
  }
  pollAlarms();
  if (appState.alarmPollTimerId) {
    return;
  }
  setState('alarmPollTimerId', window.setInterval(pollAlarms, 3000));
}

function initDashboardPage() {
  renderLog();
  loadBI();
  if (typeof loadAlarmHistory === 'function') {
    loadAlarmHistory();
  }
}

function initAssistantPage(options = {}) {
  const { tab, code, manual } = options;
  renderHistory();
  const assistantTab = tab === 'chat' ? 'chat' : 'lookup';
  switchTab(assistantTab, document.querySelector(`.tab-btn[data-tab="${assistantTab}"]`));

  if (manual) {
    const manualBtn = document.querySelector(`#panel-lookup .manual-btn[data-name="${manual}"]`);
    if (manualBtn) {
      selectManual(manualBtn);
    }
  }

  if (code) {
    if ($('searchInput')) {
      $('searchInput').value = code;
    }
    doSearch(code, manual || appState.lookupManual);
  }
}

function initOperationsPage(options = {}) {
  const { tab } = options;
  const operationsTab = ['kb', 'settings'].includes(tab) ? tab : 'work';
  switchTab(operationsTab, document.querySelector(`.tab-btn[data-tab="${operationsTab}"]`));
  loadIngestLog();
  loadKBStats();
  loadCollectionDocuments();
  loadWorkOrders();
  if (typeof loadSystemSettings === 'function') {
    loadSystemSettings();
  }
}

window.AlarmApp = {
  RAG_BASE,
  STORAGE_KEYS,
  LOOKUP_FIELDS,
  LOOKUP_SECTIONS,
  EMPTY_CHAT_HTML,
  WO_STATUS_LABEL,
  WO_PRIORITY_LABEL,
  PAGE_NAME,
  PAGE_PATHS,
  $,
  hasElements,
  navigateTo,
  readStorage,
  writeStorage,
  esc,
  toJsArg,
  formatError,
  parseJsonResponse,
  apiJson,
  apiPaged,
  currentUser,
  currentUserId,
  currentUserRole,
  authLabel,
  applyAuthChrome,
  logout,
  setResultMessage,
  appendBars,
  formatDuration,
  sizeClass,
  applySizeClass,
  setTimerClass,
  resetLookupPanels,
  buildLookupFieldBlocks,
  saveAlarmHistoryEntry,
  incrementStoredCounter,
  getTodayString,
  buildRecentDays,
  showTimer,
  loadBI,
  switchTab,
  calendarMonthKey,
  shiftCalendarMonth,
  calendarDateKey,
  renderResolvedCalendar,
  getState,
  setState,
  patchState,
  initCommonPageBindings,
  initDashboardPage,
  initAssistantPage,
  initOperationsPage,
};

document.addEventListener('DOMContentLoaded', applyAuthChrome);

