(function initAlarmAudit() {
  function formatValue(value) {
    const text = value === null || value === undefined || value === '' ? '-' : String(value);
    return text.length > 120 ? `${text.slice(0, 117)}...` : text;
  }

  function renderChanges(app, event) {
    const changes = Array.isArray(event?.changes) ? event.changes : [];
    if (!app || !changes.length) {
      return '';
    }
    return `<div class="audit-fields">${changes.map((change) => {
      const field = app.esc(change.field || '');
      const before = app.esc(formatValue(change.from));
      const after = app.esc(formatValue(change.to));
      return `<div>${field}: ${before} -> ${after}</div>`;
    }).join('')}</div>`;
  }

  function mergeEvents(workOrderHistory, issueHistory) {
    const workOrderEvents = Array.isArray(workOrderHistory)
      ? workOrderHistory.map((event) => ({ ...event, audit_source: 'work order' }))
      : [];
    const issueEvents = Array.isArray(issueHistory)
      ? issueHistory.map((event) => ({ ...event, audit_source: 'issue' }))
      : [];
    return [...workOrderEvents, ...issueEvents].sort((left, right) => {
      const leftTime = new Date(left.created_at || 0).getTime() || 0;
      const rightTime = new Date(right.created_at || 0).getTime() || 0;
      return rightTime - leftTime;
    });
  }

  window.AlarmAudit = {
    renderChanges,
    mergeEvents,
  };
})();
