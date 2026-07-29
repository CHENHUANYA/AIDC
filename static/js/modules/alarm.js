function getApp() {
  return window.AlarmApp || null;
}

function normalizeAlarmEntry(entry) {
  const timeValue = entry.time || new Date().toISOString();
  const date = new Date(timeValue);
  return {
    code: entry.code || entry.alarm_code || '',
    manual: entry.manual || '808d',
    source: entry.source || 'Manual',
    machine_id: entry.machine_id || '',
    time: Number.isNaN(date.getTime())
      ? String(timeValue).slice(11, 16)
      : date.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    date: entry.date || (Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('sv-SE')),
    isoTime: timeValue,
  };
}

function showBanner(code, manual, title, preview = '') {
  const app = getApp();
  if (!app) {
    return;
  }

  app.patchState({
    pendingCode: code,
    pendingManual: manual || '808d',
  });
  app.$('bannerCode').textContent = `ALARM ${code}`;
  if (app.$('bannerPreview')) {
    app.$('bannerPreview').textContent = preview || '';
    app.$('bannerPreview').classList.toggle('u-hidden', !preview);
  }
  app.$('bannerTitle').textContent = title || '機台警報觸發';
  app.$('alarmBanner').classList.add('show');
  window.setTimeout(dismissBanner, 30000);
}

function dismissBanner() {
  const app = getApp();
  if (!app) {
    return;
  }
  if (app.$('bannerPreview')) {
    app.$('bannerPreview').textContent = '';
    app.$('bannerPreview').classList.add('u-hidden');
  }
  app.$('alarmBanner').classList.remove('show');
}

function bannerLookup() {
  const app = getApp();
  if (!app) {
    return;
  }

  const bannerCodeText = app.$('bannerCode')?.textContent || '';
  const fallbackCode = bannerCodeText.match(/\d{2,6}/)?.[0] || '';
  const pendingCode = app.getState('pendingCode') || fallbackCode;
  const pendingManual = app.getState('pendingManual') || '808d';
  if (!pendingCode) {
    return;
  }
  if (app.PAGE_NAME !== 'assistant') {
    app.navigateTo('assistant', { code: pendingCode, manual: pendingManual, tab: 'lookup' });
    return;
  }
  dismissBanner();
  app.switchTab('lookup', document.querySelector('.tab-btn[data-tab="lookup"]'));
  const btn = document.querySelector(`#panel-lookup .manual-btn[data-name="${pendingManual}"]`);
  if (btn) {
    selectManual(btn);
  }
  app.$('searchInput').value = pendingCode;
  doSearch(pendingCode, pendingManual);
}

function addToLog(code, manual, source, machineId = '') {
  const app = getApp();
  if (!app) {
    return;
  }

  const nextLog = [normalizeAlarmEntry({
    alarm_code: code,
    manual,
    source,
    machine_id: machineId,
    time: new Date().toISOString(),
  }), ...app.getState('alarmLog')].slice(0, 100);

  app.setState('alarmLog', nextLog);
  app.writeStorage(app.STORAGE_KEYS.alarmLog, nextLog);
  updateStats();
}

function updateStats() {
  const app = getApp();
  if (!app) {
    return;
  }

  if (!app.hasElements(['warnCount', 'statCardCount', 'warnLast', 'warnLastTime', 'statCardLast'])) {
    return;
  }

  const alarmLog = app.getState('alarmLog') || [];
  const today = new Date().toLocaleDateString('sv-SE');
  const count = alarmLog.filter((item) => item.date === today).length;
  app.$('warnCount').textContent = String(count);
  app.$('statCardCount').classList.toggle('active-alarm', count > 0);

  if (!alarmLog.length) {
    app.$('warnLast').textContent = '—';
    app.$('warnLastTime').textContent = '尚未收到警報';
    app.$('statCardLast').classList.remove('active-alarm');
    return;
  }

  app.$('warnLast').textContent = alarmLog[0].code;
  app.$('warnLastTime').textContent = `${alarmLog[0].date} ${alarmLog[0].time}`;
  app.$('statCardLast').classList.add('active-alarm');
}

function renderLog() {
  const app = getApp();
  if (!app) {
    return;
  }

  const list = app.$('logList');
  if (!list) {
    return;
  }

  const alarmLog = app.getState('alarmLog') || [];
  if (!alarmLog.length) {
    list.innerHTML = '<div class="log-empty">尚未收到警報記錄</div>';
    updateStats();
    return;
  }

  list.innerHTML = alarmLog.map((item) => `
    <div class="log-item">
      <span class="log-time">${app.esc(item.date)} ${app.esc(item.time)}</span>
      <span class="log-code">${app.esc(item.code)}</span>
      <span class="log-manual">${app.esc(String(item.manual).toUpperCase())}</span>
      <span class="log-source">${app.esc(item.machine_id ? `${item.machine_id} · ${item.source}` : item.source)}</span>
    </div>`).join('');
  updateStats();
}

function refreshLogViews() {
  const app = getApp();
  if (app && typeof app.renderLog === 'function' && app.renderLog !== renderLog) {
    app.renderLog();
    return;
  }
  renderLog();
}

async function loadAlarmHistory() {
  const app = getApp();
  if (!app) {
    return;
  }

  try {
    const data = await app.apiJson('/stats/alarms');
    const normalized = (data.recent || []).slice().reverse().map(normalizeAlarmEntry).reverse();
    app.setState('alarmLog', normalized);
    app.writeStorage(app.STORAGE_KEYS.alarmLog, normalized);
    refreshLogViews();
    if (typeof app.loadBI === 'function') {
      app.loadBI();
    }
  } catch (_) {
    refreshLogViews();
  }
}

async function clearLog() {
  const app = getApp();
  if (!app) {
    return;
  }

  if (!window.confirm('確定要清空所有警報紀錄嗎？')) {
    return;
  }

  try {
    await app.apiJson('/stats/alarms', { method: 'DELETE' });
  } catch (_) {
    // Keep local clear even if server clear fails.
  }

  app.setState('alarmLog', []);
  localStorage.removeItem(app.STORAGE_KEYS.alarmLog);
  refreshLogViews();
  if (typeof app.loadBI === 'function') {
    app.loadBI();
  }
}

function testTrigger() {
  const app = getApp();
  if (!app) {
    return;
  }

  const code = app.$('testCode').value.trim() || '3000';
  const manual = app.$('testManual').value;
  showBanner(code, manual, `模擬觸發 · ${manual.toUpperCase()}`);
  addToLog(code, manual, 'Manual Test');
  refreshLogViews();
}

async function pollAlarms() {
  const app = getApp();
  if (!app) {
    return;
  }

  try {
    const data = await app.apiJson('/pending-alarms');
    if (!data.alarms?.length) {
      return;
    }

    const currentLog = app.getState('alarmLog') || [];
    const nextEntries = data.alarms.map((alarm) => {
      showBanner(alarm.alarm_code, alarm.manual, `${alarm.machine_id || 'Machine'} · ${alarm.source || 'API'}`);
      if (alarm.rag_preview && app.$('bannerPreview')) {
        app.$('bannerPreview').textContent = alarm.rag_preview;
        app.$('bannerPreview').classList.remove('u-hidden');
      }
      return normalizeAlarmEntry(alarm);
    });
    const merged = [...nextEntries.reverse(), ...currentLog].slice(0, 100);
    app.setState('alarmLog', merged);
    app.writeStorage(app.STORAGE_KEYS.alarmLog, merged);
    refreshLogViews();
    if (typeof app.loadBI === 'function') {
      app.loadBI();
    }
  } catch (_) {}
}

async function loadBI() {
  const app = getApp();
  if (!app || !app.hasElements(['biAccuracy', 'biAccBar', 'biAccuracySub'])) {
    return;
  }

  const [feedbackStats, queryStats, alarmStats, workOrderStats] = await Promise.all([
    app.apiJson('/feedback/stats').catch(() => null),
    app.apiJson('/stats/queries').catch(() => null),
    app.apiJson('/stats/alarms').catch(() => null),
    app.apiJson('/work-orders/stats').catch(() => null),
  ]);

  const good = feedbackStats?.good ?? Number(localStorage.getItem(app.STORAGE_KEYS.fbGood) || '0');
  const bad = feedbackStats?.bad ?? Number(localStorage.getItem(app.STORAGE_KEYS.fbBad) || '0');
  const total = good + bad;
  const accuracy = total > 0 ? Math.round((good / total) * 100) : null;

  const accEl = app.$('biAccuracy');
  const barEl = app.$('biAccBar');
  if (accuracy === null) {
    accEl.textContent = '--';
    accEl.className = 'bi-big';
    barEl.className = 'accuracy-bar-fill u-pct-0';
    app.$('biAccuracySub').textContent = '尚無回饋資料';
  } else {
    const stateClass = accuracy >= 80 ? 'grn' : accuracy >= 60 ? 'org' : 'red';
    accEl.textContent = `${accuracy}%`;
    accEl.className = `bi-big ${stateClass}`;
    barEl.className = `accuracy-bar-fill ${app.sizeClass('pct', accuracy, 100)}${accuracy >= 80 ? '' : accuracy >= 60 ? ' mid' : ' low'}`;
    app.$('biAccuracySub').textContent = `共 ${total} 筆回饋`;
  }

  const arc = app.$('biDonutArc');
  const circumference = 2 * Math.PI * 28;
  const filled = total > 0 ? (good / total) * circumference : 0;
  arc.setAttribute('stroke-dasharray', `${filled.toFixed(1)} ${circumference.toFixed(1)}`);
  arc.setAttribute('stroke', accuracy >= 80 ? 'var(--grn)' : accuracy >= 60 ? 'var(--org)' : 'var(--red)');
  app.$('biGoodCount').textContent = `${good} 有幫助`;
  app.$('biBadCount').textContent = `${bad} 需改善`;
  app.$('biTotalFB').textContent = `${total} 總回饋`;

  const recentTimings = queryStats?.recent?.slice(-10) || [];
  const avgMs = queryStats?.avg_ms || 0;
  app.$('biAvgTime').textContent = avgMs > 0 ? app.formatDuration(avgMs) : '--';
  const maxTiming = Math.max(...recentTimings.map((item) => item.elapsed_ms || 0), 1);
  app.$('biSparkline').innerHTML = app.appendBars(recentTimings, maxTiming, (item, maxValue) => {
    const elapsed = item.elapsed_ms || 0;
    const height = Math.max(4, Math.round((elapsed / maxValue) * 34));
    const colorClass = elapsed < 2000 ? 'u-spark-fast' : elapsed > 6000 ? 'u-spark-slow' : 'u-spark-normal';
    return `<div class="spark-bar u-flex-1 ${app.sizeClass('h', height)} ${colorClass}" title="${elapsed}ms"></div>`;
  });

  const todayCount = alarmStats?.today ?? 0;
  app.$('biTodayAlarms').textContent = String(todayCount);
  app.$('biTodayAlarmSub').textContent = todayCount === 0 ? '今日尚無警報事件' : `今日已觸發 ${todayCount} 次`;

  const recentDays = app.buildRecentDays(7);
  const dailyMap = (alarmStats?.daily || []).reduce((map, item) => {
    map[item.date] = item.count;
    return map;
  }, {});
  const dailyAlarmCounts = recentDays.map((item) => ({
    label: item.label,
    cnt: dailyMap[item.dateKey] || 0,
  }));
  const maxAlarmCount = Math.max(...dailyAlarmCounts.map((item) => item.cnt), 1);
  app.$('biAlarmChart').innerHTML = app.appendBars(dailyAlarmCounts, maxAlarmCount, (item, maxValue) => {
    const height = Math.max(3, Math.round((item.cnt / maxValue) * 55));
    const isToday = item.label === `${new Date().getMonth() + 1}/${new Date().getDate()}`;
    return `<div class="bar-col">
      <div class="bar-fill u-full-width ${app.sizeClass('h', height)} ${isToday ? 'org' : ''}"></div>
      <div class="bar-lbl">${item.label}</div>
    </div>`;
  });

  const manuals = [
    { key: '808d', label: '808D' },
    { key: '840d', label: '840D / 810D' },
    { key: '840dsl', label: '840D sl' },
    { key: 'furnace_b85t', label: 'FURNACE B85T' },
  ];
  const totalLogs = manuals.reduce((sum, manual) => sum + (alarmStats?.by_manual?.[manual.key] || 0), 0) || 1;
  app.$('biManualBody').innerHTML = manuals.map((manual) => {
    const count = alarmStats?.by_manual?.[manual.key] || 0;
    const pct = Math.round((count / totalLogs) * 100);
    return `<tr>
      <td><strong class="u-mono">${manual.label}</strong></td>
      <td>${count}</td>
      <td>${pct}%</td>
      <td><span class="mini-bar ${app.sizeClass('w', Math.max(4, pct * 1.2))}"></span></td>
    </tr>`;
  }).join('');

  const feedbackHistory = feedbackStats?.entries || app.readStorage(app.STORAGE_KEYS.fbHistory, []);
  const dailyFeedback = recentDays.map((item) => {
    const entries = feedbackHistory.filter((entry) => {
      const date = entry.date || String(entry.time || '').slice(0, 10);
      return date === item.dateKey;
    });
    return {
      label: item.label,
      good: entries.filter((entry) => (entry.type || entry.feedback) === 'good').length,
      bad: entries.filter((entry) => (entry.type || entry.feedback) === 'bad').length,
    };
  });
  const maxFeedback = Math.max(...dailyFeedback.map((item) => item.good + item.bad), 1);
  app.$('biFeedbackChart').innerHTML = app.appendBars(dailyFeedback, maxFeedback, (item, maxValue) => {
    const goodHeight = Math.max(0, Math.round((item.good / maxValue) * 70));
    const badHeight = Math.max(0, Math.round((item.bad / maxValue) * 70));
    return `<div class="bar-col u-gap-1">
      <div class="bar-fill grn u-full-width ${app.sizeClass('h', goodHeight)}"></div>
      <div class="bar-fill red u-full-width u-rounded-bottom ${app.sizeClass('h', badHeight)}"></div>
      <div class="bar-lbl">${item.label}</div>
    </div>`;
  });

  if (app.hasElements(['biWoOpen', 'biWoCompletion', 'biWoAvgHours', 'biWoUnassigned'])) {
    const openOrders = workOrderStats?.open_orders ?? 0;
    const completionRate = workOrderStats?.completion_rate ?? 0;
    const avgHours = workOrderStats?.avg_hours ?? 0;
    const unassigned = workOrderStats?.unassigned_open ?? 0;

    app.$('biWoOpen').textContent = String(openOrders);
    app.$('biWoOpenSub').textContent = `逾期 ${workOrderStats?.overdue_open ?? 0} 筆`;
    app.$('biWoCompletion').textContent = `${completionRate}%`;
    app.$('biWoCompletionSub').textContent = `已結案 ${workOrderStats?.closed_orders ?? 0} / ${workOrderStats?.total ?? 0}`;
    app.$('biWoAvgHours').textContent = avgHours > 0 ? `${avgHours}h` : '--';
    app.$('biWoAvgHoursSub').textContent = `中位數 ${workOrderStats?.median_hours ?? 0}h`;
    app.$('biWoUnassigned').textContent = String(unassigned);
    app.$('biWoUnassignedSub').textContent = `已指派 ${workOrderStats?.assigned_orders ?? 0} 筆`;
  }

  if (app.hasElements(['biWoStatusChart', 'biWoMachineBody'])) {
    const statusOrder = ['pending', 'assigned', 'in_progress', 'completed', 'verified'];
    const statusLabels = {
      pending: '待處理',
      assigned: '已指派',
      in_progress: '處理中',
      completed: '已完成',
      verified: '已驗證',
    };
    const statusCounts = statusOrder.map((status) => ({
      label: statusLabels[status],
      count: workOrderStats?.by_status?.[status] ?? 0,
      status,
    }));
    const maxStatusCount = Math.max(...statusCounts.map((item) => item.count), 1);
    app.$('biWoStatusChart').innerHTML = app.appendBars(statusCounts, maxStatusCount, (item, maxValue) => {
      const height = Math.max(3, Math.round((item.count / maxValue) * 85));
      const colorClass = item.status === 'verified'
        ? 'grn'
        : item.status === 'completed'
          ? 'org'
          : item.status === 'in_progress'
            ? ''
            : item.status === 'assigned'
              ? ''
              : 'red';
      return `<div class="bar-col">
        <div class="bar-fill u-full-width ${app.sizeClass('h', height)} ${colorClass}" title="${item.count}"></div>
        <div class="bar-lbl">${item.label}</div>
      </div>`;
    });

    const topMachines = workOrderStats?.top_machines || [];
    const totalMachineOrders = topMachines.reduce((sum, item) => sum + item.count, 0) || 1;
    app.$('biWoMachineBody').innerHTML = topMachines.length
      ? topMachines.map((item) => {
        const pct = Math.round((item.count / totalMachineOrders) * 100);
        return `<tr>
          <td><strong class="u-mono">${app.esc(item.machine_id)}</strong></td>
          <td>${item.count}</td>
          <td>${pct}%</td>
          <td><span class="mini-bar ${app.sizeClass('w', Math.max(4, pct * 1.2))}"></span></td>
        </tr>`;
      }).join('')
      : '<tr><td class="u-table-placeholder" colspan="4">尚無工單資料</td></tr>';
  }
}

const alarmApp = getApp();
if (alarmApp) {
  Object.assign(alarmApp, {
    showBanner,
    dismissBanner,
    bannerLookup,
    addToLog,
    updateStats,
    renderLog,
    loadAlarmHistory,
    clearLog,
    testTrigger,
    pollAlarms,
    loadBI,
  });
}

