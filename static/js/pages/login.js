(() => {
  const TOKEN_KEY = 'alarmAuthToken';
  const USER_KEY = 'alarmAuthUser';

  function $(id) {
    return document.getElementById(id);
  }

  function coreApi() {
    return window.AlarmCoreApi || {};
  }

  function setResult(message, state = 'error') {
    const result = $('loginResult');
    if (!result) {
      return;
    }
    result.className = `upload-result show ${state}`;
    result.textContent = message;
  }

  function setSubmitting(isSubmitting) {
    const button = $('loginSubmit');
    if (!button) {
      return;
    }
    button.disabled = isSubmitting;
    button.querySelector('span').textContent = isSubmitting ? '登入中...' : '登入系統';
  }

  function defaultPathForRole(role) {
    const api = coreApi();
    return api.roleHome ? api.roleHome(role) : '/dashboard';
  }

  function nextPath(user) {
    const url = new URL(window.location.href);
    return url.searchParams.get('next') || defaultPathForRole(user?.role);
  }

  function saveAuth(token, user) {
    const api = coreApi();
    if (api.saveAuth) {
      api.saveAuth(token, user);
      return;
    }
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  async function submitLogin(event) {
    event.preventDefault();
    const username = $('loginUsername')?.value.trim() || '';
    const password = $('loginPassword')?.value || '';
    if (!username || !password) {
      setResult('請輸入帳號與密碼。');
      return;
    }

    setSubmitting(true);
    setResult('正在驗證帳號...', 'loading');
    try {
      const api = coreApi();
      if (!api.apiJson) {
        throw new Error('登入核心腳本尚未載入，請重新整理頁面。');
      }
      const data = await api.apiJson('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (data.status !== 'ok') {
        throw new Error(data.message || '登入失敗');
      }
      saveAuth(data.token, data.user);
      setResult('登入成功，正在進入系統...', 'success');
      window.location.href = nextPath(data.user);
    } catch (error) {
      setSubmitting(false);
      setResult(error.message || '登入失敗');
    }
  }

  function bindQuickAccounts() {
    document.querySelectorAll('[data-user]').forEach((button) => {
      button.addEventListener('click', () => {
        const username = $('loginUsername');
        const password = $('loginPassword');
        if (username) {
          username.value = button.dataset.user || '';
        }
        if (password) {
          password.value = 'demo1234';
          password.focus();
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('loginForm')?.addEventListener('submit', submitLogin);
    bindQuickAccounts();
  });
})();
