(() => {
  const DEFAULT_BASE_URL = window.location.origin;
  const TOKEN_KEY = 'alarmAuthToken';
  const USER_KEY = 'alarmAuthUser';
  const PAGE_ACCESS = {
    operator: ['operator'],
    maintenance: ['maintenance'],
    supervisor: ['supervisor'],
    admin: ['admin'],
    dashboard: ['supervisor', 'admin'],
    assistant: ['operator', 'maintenance', 'supervisor', 'admin'],
    operations: ['admin'],
  };
  const ROLE_HOME = {
    operator: '/operator',
    maintenance: '/maintenance',
    supervisor: '/supervisor',
    admin: '/admin',
  };

  function readAuthToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
  }

  function readAuthUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    } catch (_) {
      return null;
    }
  }

  function saveAuth(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearAuth() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function requireAuth() {
    const page = document.body?.dataset?.page || '';
    if (page === 'login') {
      return;
    }
    if (!readAuthToken()) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    }
  }

  function roleHome(role) {
    return ROLE_HOME[role] || '/assistant';
  }

  function enforcePageAccess() {
    const page = document.body?.dataset?.page || '';
    const allowedRoles = PAGE_ACCESS[page];
    if (!allowedRoles) {
      return;
    }
    const role = readAuthUser()?.role || '';
    if (allowedRoles.includes(role)) {
      return;
    }
    window.location.href = roleHome(role);
  }

  function pageNameFromPath(path) {
    return String(path || '').replace(/^\/+/, '').split('/')[0];
  }

  function linkPathFromButton(button) {
    const onclick = button.getAttribute('onclick') || '';
    const match = onclick.match(/window\.location\.href=['"]([^'"]+)['"]/);
    return match?.[1] || '';
  }

  function pruneRoleNavigation() {
    const role = readAuthUser()?.role || '';
    document.querySelectorAll('.tab-btn').forEach((button) => {
      const page = pageNameFromPath(linkPathFromButton(button));
      const allowedRoles = PAGE_ACCESS[page];
      if (allowedRoles && !allowedRoles.includes(role)) {
        button.remove();
      }
    });
  }

  function authHeaders(headers = {}) {
    const token = readAuthToken();
    if (!token) {
      return headers;
    }
    return { ...headers, Authorization: `Bearer ${token}` };
  }

  async function parseJsonResponse(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.message || `Server error: ${response.status}`);
    }
    if (data?.status === 'error') {
      if (data.message === 'Not authenticated') {
        clearAuth();
        requireAuth();
      }
      throw new Error(data.message || 'Request failed');
    }
    return data;
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(`${DEFAULT_BASE_URL}${path}`, {
      ...options,
      headers: authHeaders(options.headers || {}),
    });
    const data = await parseJsonResponse(response);
    return data;
  }

  async function apiPaged(path, collectionKey, options = {}) {
    const separator = path.includes('?') ? '&' : '?';
    const pageLimit = Math.min(Math.max(Number(options.limit || 100), 1), 200);
    const maxPages = Math.max(Number(options.maxPages || 100), 1);
    const items = [];
    let cursor = '';
    let response = {};
    for (let page = 0; page < maxPages; page += 1) {
      const query = new URLSearchParams({ limit: String(pageLimit) });
      if (cursor) query.set('cursor', cursor);
      response = await apiJson(`${path}${separator}${query.toString()}`);
      items.push(...(Array.isArray(response?.[collectionKey]) ? response[collectionKey] : []));
      cursor = response?.next_cursor || '';
      if (!response?.has_more || !cursor) {
        return { ...response, [collectionKey]: items };
      }
    }
    throw new Error(`Pagination exceeded ${maxPages} pages for ${path}`);
  }

  window.AlarmCoreApi = {
    baseUrl: DEFAULT_BASE_URL,
    TOKEN_KEY,
    USER_KEY,
    readAuthToken,
    readAuthUser,
    saveAuth,
    clearAuth,
    requireAuth,
    enforcePageAccess,
    pruneRoleNavigation,
    roleHome,
    authHeaders,
    parseJsonResponse,
    apiJson,
    apiPaged,
  };

  requireAuth();
  enforcePageAccess();
  pruneRoleNavigation();
})();
