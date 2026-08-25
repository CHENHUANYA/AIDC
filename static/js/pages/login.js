(() => {
  const TOKEN_KEY = 'alarmAuthToken';
  const USER_KEY = 'alarmAuthUser';
  const ROLE_NEXT_PATHS = {
    operator: new Set(['/operator', '/assistant']),
    maintenance: new Set(['/maintenance', '/assistant']),
    supervisor: new Set(['/supervisor', '/dashboard', '/assistant']),
    admin: new Set(['/admin', '/dashboard', '/assistant', '/operations']),
  };

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
    button.querySelector('span').textContent = isSubmitting ? '登入中...' : '登入';
  }

  function defaultPathForRole(role) {
    const api = coreApi();
    return api.roleHome ? api.roleHome(role) : '/dashboard';
  }

  function safeNextPath(rawNext, user) {
    const fallback = defaultPathForRole(user?.role);
    if (!rawNext) {
      return fallback;
    }
    try {
      const candidate = new URL(rawNext, window.location.origin);
      const allowedPaths = ROLE_NEXT_PATHS[user?.role] || new Set();
      if (candidate.origin !== window.location.origin || !allowedPaths.has(candidate.pathname)) {
        return fallback;
      }
      return `${candidate.pathname}${candidate.search}${candidate.hash}`;
    } catch (_) {
      return fallback;
    }
  }

  function nextPath(user) {
    const url = new URL(window.location.href);
    return safeNextPath(url.searchParams.get('next'), user);
  }

  function saveAuth(token, user) {
    const api = coreApi();
    if (api.saveAuth) {
      api.saveAuth(token, user);
      return;
    }
    localStorage.removeItem(TOKEN_KEY);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  async function loadLoginConfig() {
    const hint = $('loginConfigHint');
    try {
      const api = coreApi();
      if (!api.apiJson) {
        return;
      }
      const data = await api.apiJson('/auth/login-config');
      if (!hint || data.status !== 'ok') {
        return;
      }
      hint.textContent = data.production
        ? '角色卡只會帶入使用者 ID，請輸入 bootstrap 時設定的密碼。'
        : '角色卡只會帶入使用者 ID，請輸入本機開發環境設定的密碼。';
      if (!data.initial_password_configured) {
        hint.textContent = '尚未設定初始密碼，請先執行 scripts/bootstrap_env.py 再登入。';
      }
    } catch (_) {
      // Login still works without this optional hint.
    }
  }

  async function submitLogin(event) {
    event.preventDefault();
    const username = $('loginUsername')?.value.trim() || '';
    const password = $('loginPassword')?.value || '';
    if (!username || !password) {
      setResult('請輸入使用者 ID 與密碼。');
      return;
    }

    setSubmitting(true);
    setResult('正在驗證帳號密碼...', 'loading');
    try {
      const api = coreApi();
      if (!api.apiJson) {
        throw new Error('Login API 目前無法使用，請重新整理頁面後再試。');
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
      setResult('Signed in. Opening console...', 'success');
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
        setResult(`已帶入使用者 ID：${button.dataset.user || ''}。請輸入帳號密碼繼續。`, 'loading');
        password?.focus();
      });
    });
  }

  window.AlarmLogin = { nextPath, safeNextPath };

  document.addEventListener('DOMContentLoaded', () => {
    loadLoginConfig();
    $('loginForm')?.addEventListener('submit', submitLogin);
  });
})();
