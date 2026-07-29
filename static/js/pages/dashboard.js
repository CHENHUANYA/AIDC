(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const app = window.AlarmApp;
    if (!app) {
      return;
    }

    app.initCommonPageBindings();
    app.initDashboardPage();
  });
})();


/* ─── Dashboard KPI + Table + Trend rendering ─── */

function toggleTestTools() {
  var toggle = document.getElementById('toolsToggle');
  var body = document.getElementById('toolsBody');
  toggle.classList.toggle('open');
  body.classList.toggle('show');
}

function renderAlarmTable() {
  var app = window.AlarmApp;
  if (!app) return;

  var log = app.getState('alarmLog') || [];
  var tbody = document.getElementById('alarmTableBody');
  if (!tbody) return;

  if (!log.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="table-empty">尚未收到警報記錄</td></tr>';
    return;
  }

  var rows = log.slice(0, 20).map(function(item) {
    var manual = String(item.manual || '').toUpperCase();
    var statusClass = 'unresolved';
    var statusText = '未處理';

    // Simple heuristic: newest 40% unresolved, next 30% processing, rest resolved
    var idx = log.indexOf(item);
    var ratio = idx / log.length;
    if (ratio > 0.6) {
      statusClass = 'resolved';
      statusText = '已排除';
    } else if (ratio > 0.3) {
      statusClass = 'processing';
      statusText = '處理中';
    }

    return '<tr>' +
      '<td class="u-mono u-text-small u-text-muted u-nowrap">' +
        app.esc(item.date || '') + ' ' + app.esc(item.time || '') +
      '</td>' +
      '<td class="alarm-code-cell" data-on-click="lookupAlarm" data-action-args="[' + app.toJsArg(item.code) + ',' + app.toJsArg(item.manual || '808d') + ']">' +
        app.esc(item.code) +
      '</td>' +
      '<td><span class="model-pill">' + app.esc(manual || '808D') + '</span></td>' +
      '<td class="u-text-small u-text-secondary">' +
        app.esc(item.machine_id ? item.machine_id + ' · ' + item.source : item.source || '') +
      '</td>' +
      '<td><span class="status-badge ' + statusClass + '"><span class="sb-dot"></span>' + statusText + '</span></td>' +
    '</tr>';
  });

  tbody.innerHTML = rows.join('');
}

function lookupAlarm(code, manual) {
  var app = window.AlarmApp;
  if (app) {
    app.navigateTo('assistant', { code: code, manual: manual, tab: 'lookup' });
  }
}

function renderTrendBars() {
  var app = window.AlarmApp;
  if (!app) return;

  var container = document.getElementById('trendBars');
  if (!container) return;

  var days = app.buildRecentDays(7);
  var log = app.getState('alarmLog') || [];
  var weekDayNames = ['日', '一', '二', '三', '四', '五', '六'];

  var counts = days.map(function(d) {
    return {
      label: weekDayNames[d.date.getDay()],
      dateLabel: d.label,
      count: log.filter(function(item) { return item.date === d.dateKey; }).length,
    };
  });

  var max = Math.max.apply(null, counts.map(function(c) { return c.count; }).concat([1]));

  container.innerHTML = counts.map(function(item) {
    var height = Math.max(4, Math.round((item.count / max) * 85));
    var colorClass = item.count === 0
      ? 'u-trend-empty'
      : item.count > max * 0.8
        ? 'u-trend-high'
        : item.count > max * 0.5
          ? 'u-trend-medium'
          : 'u-trend-low';

    return '<div class="trend-col">' +
      '<div class="trend-bar ' + app.sizeClass('h', height) + ' ' + colorClass + '">' +
        '<span class="trend-bar-val">' + item.count + '</span>' +
      '</div>' +
      '<div class="trend-day">' + item.label + '<br>' + item.label + '</div>' +
    '</div>';
  }).join('');

  // fix: show proper day names
  var cols = container.querySelectorAll('.trend-day');
  counts.forEach(function(item, i) {
    if (cols[i]) {
      cols[i].innerHTML = item.label + '<br><span class="u-text-xxs u-text-muted">' + item.dateLabel + '</span>';
    }
  });
}

function updateKPIs() {
  var app = window.AlarmApp;
  if (!app) return;

  var log = app.getState('alarmLog') || [];
  var today = new Date().toLocaleDateString('sv-SE');
  var todayCount = log.filter(function(item) { return item.date === today; }).length;

  // Yesterday count for delta
  var yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  var yKey = yesterday.toLocaleDateString('sv-SE');
  var yCount = log.filter(function(item) { return item.date === yKey; }).length;
  var diff = todayCount - yCount;

  document.getElementById('kpiAlarmCount').textContent = String(todayCount);
  var deltaEl = document.getElementById('kpiAlarmDelta');
  if (diff > 0) {
    deltaEl.className = 'kpi-delta up';
    deltaEl.innerHTML = '↑ ' + diff + ' vs 昨日';
  } else if (diff < 0) {
    deltaEl.className = 'kpi-delta down';
    deltaEl.innerHTML = '↓ ' + Math.abs(diff) + ' vs 昨日';
  } else {
    deltaEl.className = 'kpi-delta neutral';
    deltaEl.innerHTML = '— 與昨日相同';
  }
}

async function updateKPIsFromServer() {
  var app = window.AlarmApp;
  if (!app) return;

  try {
    var results = await Promise.all([
      app.apiJson('/stats/alarms').catch(function() { return null; }),
      app.apiJson('/work-orders/stats').catch(function() { return null; }),
      app.apiJson('/stats/queries').catch(function() { return null; }),
    ]);

    var alarmStats = results[0];
    var woStats = results[1];
    var queryStats = results[2];

    // Today alarms
    var todayCount = alarmStats?.today ?? 0;
    document.getElementById('kpiAlarmCount').textContent = String(todayCount);
    var yCount = alarmStats?.yesterday ?? 0;
    var diff = todayCount - yCount;
    var deltaEl = document.getElementById('kpiAlarmDelta');
    if (diff > 0) {
      deltaEl.className = 'kpi-delta up';
      deltaEl.innerHTML = '↑ ' + diff + ' vs 昨日';
    } else if (diff < 0) {
      deltaEl.className = 'kpi-delta down';
      deltaEl.innerHTML = '↓ ' + Math.abs(diff) + ' vs 昨日';
    } else {
      deltaEl.className = 'kpi-delta neutral';
      deltaEl.innerHTML = '— 與昨日相同';
    }

    // Pending (open work orders)
    var pending = woStats?.open_orders ?? 0;
    document.getElementById('kpiPending').textContent = String(pending);
    var overdue = woStats?.overdue_open ?? 0;
    document.getElementById('kpiPendingDelta').innerHTML = overdue > 0
      ? '<span class="u-text-danger">⚠ ' + overdue + ' 筆逾期</span>'
      : '目前待處理工單';

    // Resolved
    var resolved = woStats?.closed_orders ?? 0;
    document.getElementById('kpiResolved').textContent = String(resolved);
    var rate = woStats?.completion_rate ?? 0;
    document.getElementById('kpiResolvedDelta').innerHTML = rate > 0
      ? '完成率 ' + rate + '%'
      : '已結案工單';

    // RAG queries
    var queries = queryStats?.today ?? queryStats?.total ?? 0;
    document.getElementById('kpiQueries').textContent = String(queries);
    var avgMs = queryStats?.avg_ms ?? 0;
    document.getElementById('kpiQueriesDelta').innerHTML = avgMs > 0
      ? '平均 ' + app.formatDuration(avgMs) + '/次'
      : '今日 RAG 檢索';

  } catch (_) {
    updateKPIs();
  }
}

// Run on load
document.addEventListener('DOMContentLoaded', function() {
  // Let the standard init run first, then enhance
  setTimeout(function() {
    renderAlarmTable();
    renderTrendBars();
    updateKPIsFromServer();
  }, 200);
});

// Re-render on alarm log changes
var _origRenderLog = window.AlarmApp && window.AlarmApp.renderLog;
if (typeof _origRenderLog === 'function') {
  window.AlarmApp.renderLog = function() {
    _origRenderLog();
    renderAlarmTable();
    renderTrendBars();
    updateKPIs();
  };
}
