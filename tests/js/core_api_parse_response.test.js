const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadCoreApi(fetchImpl = async () => ({ ok: true, json: async () => ({}) })) {
  const storage = new Map();
  const context = {
    window: {
      location: {
        origin: 'http://localhost:8100',
        pathname: '/operator',
        search: '',
        href: 'http://localhost:8100/operator',
      },
    },
    document: {
      body: { dataset: { page: 'operator' } },
      querySelectorAll: () => [],
    },
    localStorage: {
      getItem: (key) => storage.get(key) || '',
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    fetch: fetchImpl,
    URLSearchParams,
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.window.localStorage = context.localStorage;
  context.globalThis = context.window;

  const source = fs.readFileSync(path.join(__dirname, '../../static/js/core/api.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'api.js' });
  return { api: context.window.AlarmCoreApi, window: context.window, storage };
}

test('authentication state, role homes, and headers remain consistent', () => {
  const { api, storage } = loadCoreApi();

  api.saveAuth('session-token', { user_id: 'supervisor01', role: 'supervisor' });
  assert.equal(api.readAuthToken(), 'session-token');
  assert.equal(api.readAuthUser().role, 'supervisor');
  assert.equal(api.roleHome('supervisor'), '/supervisor');
  assert.equal(api.roleHome('unknown'), '/assistant');
  assert.deepEqual(
    { ...api.authHeaders({ Accept: 'application/json' }) },
    { Accept: 'application/json', Authorization: 'Bearer session-token' },
  );

  api.clearAuth();
  assert.equal(storage.has(api.TOKEN_KEY), false);
  assert.equal(api.readAuthUser(), null);
});

test('API error payloads reject and expired authentication redirects to login', async () => {
  const { api, window, storage } = loadCoreApi();

  await assert.rejects(
    api.parseJsonResponse({
      ok: true,
      json: async () => ({ status: 'error', message: 'Permission denied' }),
    }),
    { message: 'Permission denied' },
  );

  storage.set(api.TOKEN_KEY, 'token');
  await assert.rejects(
    api.parseJsonResponse({
      ok: true,
      json: async () => ({ status: 'error', message: 'Not authenticated' }),
    }),
    { message: 'Not authenticated' },
  );
  assert.equal(storage.has(api.TOKEN_KEY), false);
  assert.equal(window.location.href, '/login?next=%2Foperator');
});

test('HTTP failures use server messages and a status fallback', async () => {
  const { api } = loadCoreApi();

  await assert.rejects(
    api.parseJsonResponse({ ok: false, status: 409, json: async () => ({ message: 'Conflict' }) }),
    { message: 'Conflict' },
  );
  await assert.rejects(
    api.parseJsonResponse({ ok: false, status: 503, json: async () => { throw new Error('invalid JSON'); } }),
    { message: 'Server error: 503' },
  );
});

test('apiJson sends auth headers and resolves against the application origin', async () => {
  const calls = [];
  const { api } = loadCoreApi(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ status: 'ok', value: 42 }) };
  });
  api.saveAuth('token-123', { role: 'operator' });

  const result = await api.apiJson('/health', { headers: { Accept: 'application/json' } });

  assert.equal(result.value, 42);
  assert.equal(calls[0].url, 'http://localhost:8100/health');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-123');
  assert.equal(calls[0].options.headers.Accept, 'application/json');
});

test('apiPaged accumulates cursor pages and preserves existing query parameters', async () => {
  const calls = [];
  const responses = [
    { status: 'ok', issues: [{ issue_id: 'ISS-2' }], has_more: true, next_cursor: 'next' },
    { status: 'ok', issues: [{ issue_id: 'ISS-1' }], has_more: false, next_cursor: '' },
  ];
  const { api } = loadCoreApi(async (url) => {
    calls.push(url);
    return { ok: true, json: async () => responses.shift() };
  });

  const page = await api.apiPaged('/issues/page?line_id=LINE-A', 'issues', { limit: 1 });

  assert.equal(page.issues.map((issue) => issue.issue_id).join(','), 'ISS-2,ISS-1');
  assert.match(calls[0], /line_id=LINE-A&limit=1/);
  assert.match(calls[1], /cursor=next/);
});

test('apiPaged normalizes invalid limits and enforces its page budget', async () => {
  const calls = [];
  const { api } = loadCoreApi(async (url) => {
    calls.push(url);
    return {
      ok: true,
      json: async () => ({ status: 'ok', issues: [], has_more: true, next_cursor: 'again' }),
    };
  });

  await assert.rejects(
    api.apiPaged('/issues/page', 'issues', { limit: 0, maxPages: 1 }),
    { message: 'Pagination exceeded 1 pages for /issues/page' },
  );
  assert.match(calls[0], /limit=1/);

  calls.length = 0;
  await assert.rejects(
    api.apiPaged('/issues/page', 'issues', { limit: 'invalid', maxPages: 1 }),
    { message: 'Pagination exceeded 1 pages for /issues/page' },
  );
  assert.match(calls[0], /limit=100/);
});
