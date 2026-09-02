(function initAnswerTrace(global) {
  const VALID_STATES = new Set(['complete', 'fallback', 'unavailable']);

  function app() { return global.AlarmApp || null; }

  function ensureModal() {
    let modal = document.getElementById('answerTraceModal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'answerTraceModal';
    modal.className = 'answer-trace-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'answerTraceTitle');
    modal.innerHTML = `
      <div class="answer-trace-card">
        <div class="answer-trace-head">
          <div><div id="answerTraceTitle" class="answer-trace-title">RAG 回答快照</div><div id="answerTraceId" class="answer-trace-id"></div></div>
          <button class="wo-btn alt" type="button" data-answer-trace-close>關閉</button>
        </div>
        <div id="answerTraceBody" class="answer-trace-body"></div>
      </div>`;
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.closest('[data-answer-trace-close]')) close();
    });
    document.body.appendChild(modal);
    return modal;
  }

  function close() {
    document.getElementById('answerTraceModal')?.classList.remove('show');
  }

  function field(label, value) {
    const alarmApp = app();
    return `<div class="answer-trace-field"><span>${alarmApp.esc(label)}</span><b>${alarmApp.esc(value || '-')}</b></div>`;
  }

  function render(answer) {
    const alarmApp = app();
    const modal = ensureModal();
    const state = VALID_STATES.has(answer.answer_state) ? answer.answer_state : 'complete';
    modal.querySelector('#answerTraceId').textContent = answer.answer_id || '';
    const citations = Array.isArray(answer.citations) ? answer.citations : [];
    const citationHtml = citations.length
      ? citations.map((citation, index) => `<li><b>${alarmApp.esc(citation.code || citation.title || `Citation ${index + 1}`)}</b><span>${alarmApp.esc(citation.source_file || citation.source || '')}</span><small>${alarmApp.esc(citation.locator || (citation.page ? `page ${citation.page}` : ''))}</small></li>`).join('')
      : '<li class="answer-trace-empty">無 citation</li>';
    modal.querySelector('#answerTraceBody').innerHTML = `
      <div class="answer-trace-state state-${state}">${alarmApp.esc(state)}</div>
      <div class="answer-trace-grid">
        ${field('Provider', answer.provider)}${field('Model', answer.model)}${field('產生時間', answer.created_at)}
        ${field('Latency', `${Number(answer.elapsed_ms || 0)} ms`)}${field('Tokenizer', answer.tokenizer_version)}${field('Retrieval', answer.retrieval_version)}
      </div>
      <section><h3>Query</h3><div class="answer-trace-text">${alarmApp.esc(answer.query || '')}</div></section>
      <section><h3>Answer</h3><div class="answer-trace-text">${alarmApp.esc(answer.answer || '')}</div></section>
      <section><h3>Citations</h3><ul class="answer-trace-citations">${citationHtml}</ul></section>`;
  }

  function renderUnavailable(answerId, message) {
    render({ answer_id: answerId, answer_state: 'unavailable', answer: message || '回答快照目前無法取得。', citations: [] });
  }

  async function open(answerId) {
    const alarmApp = app();
    ensureModal().classList.add('show');
    renderUnavailable(answerId, '載入中…');
    try {
      const data = await alarmApp.apiJson(`/rag/answers/${encodeURIComponent(answerId)}`);
      if (data?.status !== 'ok' || !data.answer) {
        renderUnavailable(answerId, data?.message || '回答快照不存在。');
        return;
      }
      render(data.answer);
    } catch (error) {
      renderUnavailable(answerId, alarmApp.formatError(error, '回答快照無法取得。'));
    }
  }

  global.AnswerTrace = { open, close };
})(window);
