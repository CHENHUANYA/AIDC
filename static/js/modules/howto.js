const HOWTO_BASE = window.AlarmCoreApi?.baseUrl || window.location.origin;

const howtoState = {
  collection: '808d',
  topic: null,
  topics: [],
};

function howtoEsc(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function howtoJson(path) {
  const response = await fetch(`${HOWTO_BASE}${path}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || `HTTP ${response.status}`);
  }
  return data;
}

function renderHowtoTopics() {
  const host = document.getElementById('howtoTopics');
  if (!host) {
    return;
  }
  if (!howtoState.topics.length) {
    host.innerHTML = '<span class="ex-label">此手冊尚無 HOW-TO 主題</span>';
    return;
  }
  host.innerHTML = howtoState.topics.map((topic) => {
    const isActive = topic === howtoState.topic;
    return `<button class="ex-btn${isActive ? ' active' : ''}" onclick="loadHowtoTopic('${howtoEsc(topic)}')">${howtoEsc(topic)}</button>`;
  }).join('');
}

function renderHowtoSteps(entries) {
  const host = document.getElementById('howtoSteps');
  if (!host) {
    return;
  }
  if (!entries.length) {
    host.innerHTML = '<div class="log-empty">沒有可顯示的步驟</div>';
    return;
  }
  host.innerHTML = entries.map((entry, idx) => `
    <div class="field-block">
      <div class="field-label fl-remedy">STEP ${idx + 1} · ${howtoEsc(entry.title || 'Untitled')}</div>
      <div class="field-content">${howtoEsc(entry.instruction || '')}</div>
      <div class="wo-note" style="margin-top:8px">Softkey: ${howtoEsc(entry.softkey_path || '-')}</div>
      <div class="wo-note">Note: ${howtoEsc(entry.note || '-')}</div>
    </div>
  `).join('');
}

async function loadHowtoTopics() {
  try {
    const data = await howtoJson(`/v1/${howtoState.collection}/howto`);
    howtoState.topics = data.topics || [];
    if (!howtoState.topic || !howtoState.topics.includes(howtoState.topic)) {
      howtoState.topic = howtoState.topics[0] || null;
    }
    renderHowtoTopics();
    if (howtoState.topic) {
      await loadHowtoTopic(howtoState.topic);
    } else {
      renderHowtoSteps([]);
    }
  } catch (error) {
    document.getElementById('howtoSteps').innerHTML = `<div class="log-empty">${howtoEsc(error.message)}</div>`;
  }
}

async function loadHowtoTopic(topic) {
  howtoState.topic = topic;
  renderHowtoTopics();
  const title = document.getElementById('howtoTitle');
  if (title) {
    title.textContent = `HOW-TO · ${topic}`;
  }
  try {
    const data = await howtoJson(`/v1/${howtoState.collection}/howto/${encodeURIComponent(topic)}`);
    renderHowtoSteps(data.entries || []);
  } catch (error) {
    renderHowtoSteps([]);
    document.getElementById('howtoSteps').innerHTML = `<div class="log-empty">${howtoEsc(error.message)}</div>`;
  }
}

function selectHowtoManual(button) {
  document.querySelectorAll('.manual-btn[data-col]').forEach((node) => node.classList.remove('active'));
  button.classList.add('active');
  howtoState.collection = button.dataset.col || '808d';
  loadHowtoTopics();
}

document.addEventListener('DOMContentLoaded', () => {
  loadHowtoTopics();
});
