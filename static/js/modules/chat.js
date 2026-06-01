function getApp() {
  return window.AlarmApp || null;
}

function selectChatManual(btn) {
  const app = getApp();
  if (!app) {
    return;
  }

  document.querySelectorAll('.chat-manual-btn').forEach((node) => node.classList.remove('active'));
  btn.classList.add('active');
  app.setState('chatCollection', btn.dataset.col);
}

function chatSuggest(text) {
  const app = getApp();
  if (!app) {
    return;
  }

  app.$('chatInput').value = text;
  sendChat();
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
}

function chatKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChat();
  }
}

function clearChat() {
  const app = getApp();
  if (!app) {
    return;
  }

  app.setState('chatHistory', []);
  if (app.$('chatMessages')) {
    app.$('chatMessages').innerHTML = app.EMPTY_CHAT_HTML;
  }
  if (app.$('chatSuggestions')) {
    app.$('chatSuggestions').style.display = 'flex';
  }
}

function stripMetaComments(text) {
  return text.replace(/<!--\s*(?:PAGE|TITLE|CODE):[^>]+-->/g, '').replace(/^\n+/, '');
}

function renderMarkdown(text) {
  const app = getApp();
  if (!app) {
    return text;
  }

  let html = app.esc(text);
  html = html.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+?)\*/g, '<em>$1</em>');
  html = html.replace(/`([^`]+?)`/g, '<code>$1</code>');
  html = html.replace(/^(#{1,3})\s+(.+)$/gm, (_, hashes, title) => {
    const level = hashes.length + 1;
    return `<h${level} class="md-heading">${title}</h${level}>`;
  });
  html = html.replace(/\n/g, '<br>');
  return html;
}

function canSaveAssistantMessage(content) {
  const text = String(content || '').trim();
  return Boolean(text)
    && !text.startsWith('Error:')
    && !text.includes('無法連線至 LLM')
    && !text.includes('對話查詢失敗');
}

function appendMessage(role, content) {
  const app = getApp();
  if (!app) {
    return;
  }

  const container = app.$('chatMessages');
  app.$('chatEmpty')?.remove();
  app.$('chatSuggestions').style.display = 'none';

  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="msg-avatar">${role === 'user' ? '👤' : '🤖'}</div>
    <div>
      <div class="msg-bubble"></div>
      <div class="msg-time">${new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}</div>
    </div>`;

  const bubble = div.querySelector('.msg-bubble');
  if (role === 'assistant') {
    // Extract metadata before stripping
    const pageTag = content.match(/<!-- PAGE:(\d+) -->/)?.[1] || null;
    const codeTag = content.match(/<!-- CODE:(\d+) -->/)?.[1] || null;
    const titleTag = content.match(/<!-- TITLE:([^-]+?)\s*-->/)?.[1]?.trim() || null;
    const collection = app.getState('chatCollection') || '808d';

    const cleaned = stripMetaComments(content);
    bubble.innerHTML = renderMarkdown(cleaned);

    // Source citation
    if (pageTag || codeTag) {
      const srcParts = ['<span class="src-icon">📖</span>'];
      if (codeTag) srcParts.push(`Alarm <span class="src-page">${codeTag}</span>`);
      if (pageTag) srcParts.push(`P.<span class="src-page">${pageTag}</span>`);
      srcParts.push(`<span class="src-manual">${collection.toUpperCase()}</span>`);
      if (titleTag) srcParts.push(`· ${app.esc(titleTag)}`);
      bubble.innerHTML += `<div class="msg-source">${srcParts.join(' ')}</div>`;
    }

    if (canSaveAssistantMessage(content)) {
      const msgIdx = container.querySelectorAll('.msg.assistant').length;
      bubble.innerHTML += `<div class="msg-actions">
        <button class="msg-act-btn" onclick="saveMsgToKB(this, ${msgIdx})" title="儲存此回覆為維修記錄">💾 儲存到知識庫</button>
      </div>`;
    }
  } else {
    bubble.textContent = content;
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function showTyping() {
  const app = getApp();
  if (!app) {
    return;
  }

  const container = app.$('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.id = 'typingIndicator';
  div.innerHTML = `
    <div class="msg-avatar">🤖</div>
    <div class="msg-bubble typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTyping() {
  const app = getApp();
  if (!app) {
    return;
  }
  app.$('typingIndicator')?.remove();
}

async function sendChat() {
  const app = getApp();
  if (!app) {
    return;
  }

  const input = app.$('chatInput');
  const sendBtn = app.$('chatSendBtn');
  const text = input.value.trim();
  if (!text) {
    return;
  }

  sendBtn.disabled = true;
  input.value = '';
  input.style.height = 'auto';

  appendMessage('user', text);
  const requestHistory = [...app.getState('chatHistory'), { role: 'user', content: text }];
  app.setState('chatHistory', requestHistory);
  showTyping();

  try {
    const data = await app.apiJson(`/v1/${app.getState('chatCollection')}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: requestHistory,
        stream: false,
        temperature: 0.7,
        max_tokens: 1024,
      }),
    });
    const content = data?.choices?.[0]?.message?.content || '抱歉，系統目前沒有回傳內容。';
    removeTyping();
    appendMessage('assistant', content);
    app.setState('chatHistory', [...requestHistory, { role: 'assistant', content }].slice(-12));
  } catch (error) {
    removeTyping();
    appendMessage('assistant', `⚠️ ${app.formatError(error, '對話查詢失敗')}\n請確認 alarm_rag 服務是否正常運行。`);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

async function saveMsgToKB(btn, msgIdx) {
  const app = getApp();
  if (!app) return;

  const bubbles = document.querySelectorAll('.msg.assistant .msg-bubble');
  const bubble = bubbles[msgIdx];
  if (!bubble) return;

  // Get raw text content (strip HTML)
  const tempDiv = document.createElement('div');
  tempDiv.innerHTML = bubble.innerHTML;
  // Remove source and actions from clone
  tempDiv.querySelectorAll('.msg-source,.msg-actions').forEach(el => el.remove());
  const text = tempDiv.textContent.trim();
  if (!text) return;

  const collection = app.getState('chatCollection') || '808d';

  btn.disabled = true;
  btn.textContent = '⏳ 儲存中...';

  try {
    await app.apiJson(`/v1/${collection}/ingest-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text,
        source: 'chat',
        title: 'Chat 對話記錄',
      }),
    });
    btn.className = 'msg-act-btn saved';
    btn.textContent = '✅ 已儲存';
    btn.onclick = null;
  } catch (error) {
    btn.textContent = '❌ 儲存失敗';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = '💾 儲存到知識庫'; }, 2000);
  }
}

const chatApp = getApp();
if (chatApp) {
  Object.assign(chatApp, {
    selectChatManual,
    chatSuggest,
    autoResize,
    chatKeydown,
    clearChat,
    sendChat,
    saveMsgToKB,
  });
}
