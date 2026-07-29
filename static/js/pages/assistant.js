(() => {
  function filterRefTable() {
    const input = document.getElementById('refSearch');
    const filter = (input?.value || '').toLowerCase();
    const rows = document.querySelectorAll('#refTable tbody tr');
    let visible = 0;
    rows.forEach((row) => {
      const text = row.textContent.toLowerCase();
      const show = !filter || text.indexOf(filter) >= 0;
      row.classList.toggle('ref-hidden', !show);
      if (show) {
        visible += 1;
      }
    });
    const countEl = document.getElementById('refCount');
    if (countEl) {
      countEl.textContent = `顯示 ${visible} / ${rows.length} 筆`;
    }
  }

  async function loadPhase2Reference() {
    const app = window.AlarmApp;
    const panel = document.getElementById('panel-reference');
    if (!app || !panel) {
      return;
    }
    const collection = app.getState?.('lookupManual') || '808d';
    let host = document.getElementById('phase2ReferenceHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'phase2ReferenceHost';
      host.className = 'card u-mt-20';
      panel.querySelector('.container')?.appendChild(host);
    }
    host.innerHTML = '<div class="section-label">參考資料（Phase 2）</div><div class="log-empty">載入中...</div>';
    try {
      const [actionsRes, errorsRes] = await Promise.all([
        fetch(`/v1/${encodeURIComponent(collection)}/reference/action-numbers`),
        fetch(`/v1/${encodeURIComponent(collection)}/reference/error-codes`),
      ]);
      const actions = await actionsRes.json();
      const errors = await errorsRes.json();
      const actionRows = (actions.entries || []).map((item) =>
        `<tr><td class="ref-code">${app.esc(item.action_number || '')}</td><td>${app.esc(item.reaction || '')}</td><td>${app.esc(item.effect || '')}</td><td>${app.esc(item.recovery || '')}</td></tr>`
      ).join('');
      const errorRows = (errors.entries || []).map((item) =>
        `<tr><td class="ref-code">${app.esc(item.hex || '')}</td><td>${app.esc(item.code || '')}</td><td>${app.esc(item.meaning || '')}</td><td>${app.esc(item.remedy || '')}</td></tr>`
      ).join('');
      host.innerHTML = `
        <div class="section-label">參考資料（Phase 2）</div>
        <details open>
          <summary class="u-reference-summary">840D Action Numbers（${(actions.entries || []).length}）</summary>
          <div class="u-overflow-x">
            <table class="ref-table">
              <thead><tr><th>Action</th><th>Reaction</th><th>影響</th><th>復原方式</th></tr></thead>
              <tbody>${actionRows || '<tr><td colspan="4">沒有資料</td></tr>'}</tbody>
            </table>
          </div>
        </details>
        <details class="u-mt-12">
          <summary class="u-reference-summary">Error Codes（300500，${(errors.entries || []).length}）</summary>
          <div class="u-overflow-x">
            <table class="ref-table">
              <thead><tr><th>HEX</th><th>Code</th><th>意義</th><th>處置方式</th></tr></thead>
              <tbody>${errorRows || '<tr><td colspan="4">沒有資料</td></tr>'}</tbody>
            </table>
          </div>
        </details>
      `;
    } catch (error) {
      host.innerHTML = `<div class="section-label">參考資料（Phase 2）</div><div class="log-empty">${app.esc(error?.message || error)}</div>`;
    }
  }

  window.filterRefTable = filterRefTable;

  document.addEventListener('DOMContentLoaded', () => {
    const app = window.AlarmApp;
    if (!app) {
      return;
    }

    const url = new URL(window.location.href);
    app.initCommonPageBindings();
    app.initAssistantPage({
      tab: url.searchParams.get('tab'),
      code: url.searchParams.get('code'),
      manual: url.searchParams.get('manual'),
    });
    const refTab = document.querySelector('.tab-btn[data-tab="reference"]');
    refTab?.addEventListener('click', () => setTimeout(loadPhase2Reference, 50));
  });
})();
