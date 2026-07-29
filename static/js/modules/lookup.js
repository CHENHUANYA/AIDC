function getApp() {
  return window.AlarmApp || null;
}

function classifyAlarmCode(codeStr) {
  var num = parseInt(codeStr, 10);
  if (isNaN(num)) return { type: 'unknown', label: '其他', severity: 'info' };
  if (num < 10000)   return { type: 'NCK',     label: 'NCK 系統核心',       severity: 'high' };
  if (num < 20000)   return { type: 'Channel', label: 'Channel 通道',       severity: 'mid' };
  if (num < 30000)   return { type: 'Axis',    label: '軸 / 主軸',          severity: 'high' };
  if (num < 70000)   return { type: 'Cycle',   label: 'Cycle 週期',         severity: 'mid' };
  if (num >= 300000 && num < 400000) return { type: 'Drive', label: '驅動 SIMODRIVE/SINAMICS', severity: 'high' };
  if (num >= 400000 && num < 500000) return { type: 'PLC',   label: 'PLC',                    severity: 'mid' };
  return { type: 'Other', label: '其他', severity: 'info' };
}

function getAlarmTypeBadge(codeStr, reactionText) {
  var num = parseInt(codeStr, 10);
  if (isNaN(num)) return '';
  var isFault = false;
  if (num >= 300000 && num < 400000) {
    isFault = true;
  }
  if (reactionText) {
    var lower = reactionText.toLowerCase();
    if (lower.indexOf('nc not ready') >= 0 || lower.indexOf('nc stop') >= 0 || lower.indexOf('mode group not ready') >= 0) {
      isFault = true;
    }
    if (lower.indexOf('no reaction') >= 0 || lower.indexOf('display') >= 0) {
      isFault = false;
    }
  }
  if (isFault) {
    return '<span class="alarm-type-badge fault">⛔ FAULT</span>';
  }
  return '<span class="alarm-type-badge alarm-warn">⚠️ ALARM</span>';
}

function renderResultActions(code, manual) {
  var app = getApp();
  if (!app) return '';
  return '<div class="result-actions" id="resultActions">' +
    '<button class="result-action-btn purple" data-on-click="askInChat" data-action-args="[' + app.toJsArg(code) + ']">' +
      '📨 在 Chat 中提問' +
    '</button>' +
    '<button class="result-action-btn" data-on-click="copyAlarmCode" data-action-args="[' + app.toJsArg(code) + ']">' +
      '📋 複製警報碼' +
    '</button>' +
  '</div>';
}

function askInChat(code) {
  var app = getApp();
  if (!app) return;
  if (!app.$('chatInput') || !app.$('panel-chat')) {
    app.navigateTo('assistant', { tab: 'chat' });
    return;
  }
  app.switchTab('chat', document.querySelector('.tab-btn[data-tab="chat"]'));
  var input = app.$('chatInput');
  if (input) {
    input.value = '請詳細解釋警報 ' + code + ' 的原因及處理步驟';
    input.focus();
  }
}

function copyAlarmCode(code) {
  navigator.clipboard.writeText(code).then(function() {
    var btn = document.querySelector('#resultActions .result-action-btn:last-child');
    if (btn) {
      btn.textContent = '✅ 已複製';
      setTimeout(function() { btn.innerHTML = '📋 複製警報碼'; }, 1500);
    }
  });
}

function selectManual(btn) {
  const app = getApp();
  if (!app) {
    return;
  }

  document.querySelectorAll('#panel-lookup .manual-btn').forEach((node) => node.classList.remove('active'));
  btn.classList.add('active');
  app.setState('lookupManual', btn.dataset.name);
  app.resetLookupPanels();
}

function setExample(value) {
  const app = getApp();
  if (!app) {
    return;
  }

  app.$('searchInput').value = value;
  doSearch();
}

async function doSearch(code, manual) {
  const app = getApp();
  if (!app) {
    return;
  }

  const query = code || app.$('searchInput').value.trim();
  if (!query) {
    return;
  }

  const useManual = manual || app.getState('lookupManual');
  app.setState('lastQuery', query);
  app.setState('lastAnswerId', '');
  app.resetLookupPanels();
  app.$('timerRow').classList.remove('show');
  app.$('fbThanks').classList.remove('show');
  document.querySelectorAll('.fb-btn').forEach((node) => node.classList.remove('sel-good', 'sel-bad'));
  app.$('loading').classList.add('show');
  app.$('searchBtn').disabled = true;

  const startedAt = Date.now();
  const isAlarmCode = /^\d{2,6}$/.test(query.trim());

  try {
    if (isAlarmCode) {
      const lookupRes = await fetch(`${app.RAG_BASE}/v1/${useManual}/lookup?code=${encodeURIComponent(query.trim())}`, {
        headers: window.AlarmCoreApi?.authHeaders?.({}) || {},
      });
      const lookupData = await app.parseJsonResponse(lookupRes);
      const lookupReceivedAt = Date.now();
      if (lookupData.found) {
        app.showTimer(lookupReceivedAt - startedAt, 0);
        app.resetLookupPanels();
        renderLookupResult(lookupData, query);
        saveHistoryFromLookup(lookupData, useManual);
        return;
      }
    }

    const response = await fetch(`${app.RAG_BASE}/v1/${useManual}/chat/completions`, {
      method: 'POST',
      headers: window.AlarmCoreApi?.authHeaders?.({ 'Content-Type': 'application/json' }) || { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [{ role: 'user', content: query }],
        stream: false,
        temperature: 0.1,
        max_tokens: 1024,
      }),
    });
    const receivedAt = Date.now();
    const data = await app.parseJsonResponse(response);
    const content = data?.choices?.[0]?.message?.content || '';
    if (!content) {
      throw new Error('Empty response');
    }
    app.setState('lastAnswerId', data?.rag?.answer_id || data?.id || '');

    const retrievalHeader = response.headers.get('X-Retrieval-Time');
    const retrievalMs = retrievalHeader ? Number.parseInt(retrievalHeader, 10) : Math.round((receivedAt - startedAt) * 0.3);
    const totalElapsed = Date.now() - startedAt;
    app.showTimer(retrievalMs, Math.max(totalElapsed - retrievalMs, 0));

    app.resetLookupPanels();
    renderResult(content, query);
    saveHistory(query, content, useManual);
  } catch (error) {
    app.resetLookupPanels();
    app.$('errorMsg').textContent = `無法連線至 RAG 伺服器，請確認 alarm_rag 服務正在運行。\n\n${error.message}`;
    app.$('errorBox').classList.add('show');
  } finally {
    app.$('searchBtn').disabled = false;
  }
}

function renderLookupResult(data, query) {
  const app = getApp();
  if (!app) {
    return;
  }

  const code = data.code || query;
  app.$('resCode').textContent = code;
  app.$('resTitle').textContent = data.title || '—';
  app.$('resPage').textContent = data.page || '—';

  const text = data.text || '';
  const getFieldValue = (label) => {
    const regex = new RegExp(`(?:^|\\n)\\s*${label}\\s*:\\s*([\\s\\S]*?)(?=\\n\\s*(?:${app.LOOKUP_SECTIONS})\\s*:|$)`, 'i');
    return text.match(regex)?.[1]?.trim() || null;
  };

  // Add type badge + category badge to header
  const classify = classifyAlarmCode(code);
  const reactionText = getFieldValue('Reaction') || '';
  const headerEl = app.$('resCode')?.parentElement;
  if (headerEl) {
    const existingBadges = headerEl.querySelectorAll('.alarm-type-badge,.alarm-category-badge');
    existingBadges.forEach(function(b) { b.remove(); });

    const typeBadgeHtml = getAlarmTypeBadge(code, reactionText);
    const catBadgeHtml = '<span class="alarm-category-badge">' + app.esc(classify.label) + '</span>';
    headerEl.insertAdjacentHTML('beforeend', typeBadgeHtml + catBadgeHtml);
  }

  app.$('resBody').innerHTML = (app.buildLookupFieldBlocks(getFieldValue) || `<div class="field-content">${app.esc(text)}</div>`) + renderLookupMetadata(data.metadata);
  app.$('resultCard').classList.add('show');

  // Add action buttons after feedback row
  const existingActions = document.getElementById('resultActions');
  if (existingActions) existingActions.remove();
  const feedbackRow = app.$('resultCard')?.querySelector('.feedback-row');
  if (feedbackRow) {
    feedbackRow.insertAdjacentHTML('afterend', renderResultActions(code, app.getState('lookupManual')));
  } else {
    app.$('resultCard')?.insertAdjacentHTML('afterend', renderResultActions(code, app.getState('lookupManual')));
  }
}

function renderLookupMetadata(metadata) {
  var app = getApp();
  if (!app || !metadata) {
    return '';
  }

  var fields = [
    ['集合', metadata.collection],
    ['來源', metadata.source || metadata.source_file],
    ['文件', metadata.doc_id],
    ['類型', metadata.kind],
    ['匯入時間', metadata.imported_at],
  ].filter(function(item) {
    return item[1] !== undefined && item[1] !== null && String(item[1]).trim() !== '';
  });

  if (!fields.length) {
    return '';
  }

  return '<div class="lookup-meta">' +
    '<div class="lookup-meta-title">來源 metadata</div>' +
    fields.map(function(item) {
      return '<span class="lookup-meta-pill"><b>' + app.esc(item[0]) + '</b>' + app.esc(String(item[1])) + '</span>';
    }).join('') +
  '</div>';
}

function renderResult(content, query) {
  const app = getApp();
  if (!app) {
    return;
  }

  // Enhanced regex: handles both English and Chinese field labels
  const getBlock = (label) => {
    // Try English bold format: **Parameters:** ... or **參數：** ...
    const regexBold = new RegExp(`\\*\\*${label}[^*]*\\*\\*[：:]?\\s*([\\s\\S]*?)(?=\\n\\*\\*|$)`, 'i');
    const matchBold = content.match(regexBold);
    if (matchBold) return matchBold[1].trim();

    // Try plain format: Parameters: ... or 參數: ...
    const SECTIONS = app.LOOKUP_SECTIONS;
    const regexPlain = new RegExp(`(?:^|\\n)\\s*${label}\\s*[：:]\\s*([\\s\\S]*?)(?=\\n\\s*(?:${SECTIONS})\\s*[：:]|$)`, 'i');
    const matchPlain = content.match(regexPlain);
    if (matchPlain) return matchPlain[1].trim();

    return null;
  };

  // Chinese label aliases
  const getBlockWithAliases = (label) => {
    const aliases = {
      'Parameters': ['Parameters', '參數'],
      'Explanation': ['Explanation', '說明', '解釋'],
      'Reaction': ['Reaction', '反應', '系統反應'],
      'Remedy': ['Remedy', '處置', '處理方式', '修復'],
      'Program continuation': ['Program continuation', '程式續行', '繼續執行'],
    };
    const candidates = aliases[label] || [label];
    for (const alias of candidates) {
      const result = getBlock(alias);
      if (result) return result;
    }
    return null;
  };

  const tagPage = content.match(/<!--\s*PAGE:(\d+)\s*-->/)?.[1] || null;
  const tagTitle = content.match(/<!--\s*TITLE:([^-]+?)\s*-->/)?.[1] || null;
  const tagCode = content.match(/<!--\s*CODE:(\d+)\s*-->/)?.[1] || null;
  const cleanContent = content.replace(/<!--\s*(?:PAGE|TITLE|CODE):[^>]+-->/g, '').replace(/^\n/, '');
  const codeMatch = cleanContent.match(/\*\*Alarm\s+(\d+)[^*]*\*\*[：:]?\s*(.*)/i) || cleanContent.match(/Alarm\s+(\d+)[：:]?\s*(.*)/i);
  const llmCode = codeMatch ? codeMatch[1] : query.match(/\d{2,6}/)?.[0] || '?';
  const llmTitle = codeMatch ? codeMatch[2].replace(/\*\*/g, '').trim() : '';
  const llmPage = cleanContent.match(/\*{0,2}Manual Page\*{0,2}[：:?\s]*\*{0,2}(\d+)\*{0,2}/i)?.[1] || null;

  const finalCode = tagCode || llmCode;
  app.$('resCode').textContent = finalCode;
  app.$('resTitle').textContent = tagTitle || llmTitle || '—';
  app.$('resPage').textContent = tagPage || llmPage || '—';

  // Add type badge + category badge
  const classify = classifyAlarmCode(finalCode);
  const reactionText = getBlockWithAliases('Reaction') || '';
  const headerEl = app.$('resCode')?.parentElement;
  if (headerEl) {
    const existingBadges = headerEl.querySelectorAll('.alarm-type-badge,.alarm-category-badge');
    existingBadges.forEach(function(b) { b.remove(); });

    const typeBadgeHtml = getAlarmTypeBadge(finalCode, reactionText);
    const catBadgeHtml = '<span class="alarm-category-badge">' + app.esc(classify.label) + '</span>';
    headerEl.insertAdjacentHTML('beforeend', typeBadgeHtml + catBadgeHtml);
  }

  let html = '';
  if (cleanContent.includes('was not found') || cleanContent.includes('not found in') || cleanContent.includes('無法在手冊中找到')) {
    const missingCode = query.match(/\d{2,6}/)?.[0] || query;
    app.$('resCode').textContent = missingCode;
    app.$('resTitle').textContent = '未找到此警報代碼';
    app.$('resPage').textContent = '—';
    html = `<div class="not-found-box">
      <div class="nf-icon">🔍</div>
      <div class="nf-title">Alarm ${app.esc(missingCode)} 未在手冊中找到</div>
      <div class="nf-sub">請確認警報代碼是否正確，或嘗試切換其他手冊</div>
    </div>`;
  } else {
    html = app.buildLookupFieldBlocks(getBlockWithAliases) || `<div class="field-content">${app.esc(cleanContent)}</div>`;
  }

  app.$('resBody').innerHTML = html;
  app.$('resultCard').classList.add('show');

  // Add action buttons
  const existingActions = document.getElementById('resultActions');
  if (existingActions) existingActions.remove();
  const feedbackRow = app.$('resultCard')?.querySelector('.feedback-row');
  if (feedbackRow) {
    feedbackRow.insertAdjacentHTML('afterend', renderResultActions(finalCode, app.getState('lookupManual')));
  } else {
    app.$('resultCard')?.insertAdjacentHTML('afterend', renderResultActions(finalCode, app.getState('lookupManual')));
  }
}

function saveHistoryFromLookup(data, manual) {
  const app = getApp();
  if (!app) {
    return;
  }

  app.saveAlarmHistoryEntry({
    code: data.code || '?',
    title: data.title || data.code || '?',
    manual,
    time: Date.now(),
  });
}

function saveHistory(query, content, manual) {
  const app = getApp();
  if (!app) {
    return;
  }

  const code = content.match(/Alarm\s+(\d+)/i)?.[1] || query.match(/\d{2,6}/)?.[0] || query;
  const title = content.match(/\*\*Alarm[^*]*\*\*[：:]?\s*([^\n*]+)/i)?.[1]?.trim() || query;
  app.saveAlarmHistoryEntry({ code, title, manual, time: Date.now() });
}

function renderHistory() {
  const app = getApp();
  if (!app) {
    return;
  }

  const history = app.readStorage(app.STORAGE_KEYS.alarmHistory, []);
  const section = app.$('histSection');
  const grid = app.$('histGrid');
  if (!section || !grid) {
    return;
  }
  if (!history.length) {
    section.classList.add('u-hidden');
    return;
  }

  section.classList.remove('u-hidden');
  grid.innerHTML = history.map((item) => `
    <div class="hist-item" data-on-click="loadHist" data-action-args="[${app.toJsArg(item.code)}, ${app.toJsArg(item.manual)}]">
      <span class="hist-code">${app.esc(item.code)}</span>
      <span class="hist-title">${app.esc(item.title)}</span>
      <span class="hist-manual">${app.esc(item.manual.toUpperCase())}</span>
    </div>`).join('');
}

function loadHist(code, manual) {
  const app = getApp();
  if (!app) {
    return;
  }

  if (!app.$('searchInput') || !app.$('panel-lookup')) {
    app.navigateTo('assistant', { code, manual, tab: 'lookup' });
    return;
  }
  app.switchTab('lookup', document.querySelector('.tab-btn[data-tab="lookup"]'));
  const btn = document.querySelector(`#panel-lookup .manual-btn[data-name="${manual}"]`);
  if (btn) {
    selectManual(btn);
  }
  app.$('searchInput').value = code;
  doSearch(code, manual);
}

function sendFeedback(type) {
  const app = getApp();
  if (!app) {
    return;
  }

  document.querySelectorAll('.fb-btn').forEach((node) => node.classList.remove('sel-good', 'sel-bad'));
  document.querySelector(`.fb-btn.${type === 'good' ? 'good' : 'bad'}`)?.classList.add(`sel-${type}`);
  app.$('fbThanks').classList.add('show');

  const history = app.readStorage(app.STORAGE_KEYS.fbHistory, []);
  history.unshift({ type, date: app.getTodayString(), t: Date.now() });
  app.writeStorage(app.STORAGE_KEYS.fbHistory, history.slice(0, 200));
  app.incrementStoredCounter(type === 'good' ? app.STORAGE_KEYS.fbGood : app.STORAGE_KEYS.fbBad);

  app.apiJson('/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: app.getState('lastQuery'),
      collection: app.getState('lookupManual'),
      feedback: type,
      answer_id: app.getState('lastAnswerId') || '',
      role: app.PAGE_NAME === 'maintenance' ? 'maintenance' : 'assistant',
    }),
  }).catch(() => {});
}

const lookupApp = getApp();
if (lookupApp) {
  Object.assign(lookupApp, {
    selectManual,
    setExample,
    doSearch,
    renderLookupResult,
    renderResult,
    saveHistoryFromLookup,
    saveHistory,
    renderHistory,
    loadHist,
    sendFeedback,
    classifyAlarmCode,
    askInChat,
    copyAlarmCode,
  });
}
