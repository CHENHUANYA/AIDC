const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadCoreApi() {
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
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.window.localStorage = context.localStorage;
  context.globalThis = context.window;

  const source = fs.readFileSync(path.join(__dirname, '../../static/js/core/api.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'api.js' });
  return { api: context.window.AlarmCoreApi, window: context.window, storage };
}

async function assertRejectsWithMessage(promise, expected) {
  try {
    await promise;
  } catch (error) {
    assert.strictEqual(error.message, expected);
    return;
  }
  throw new Error(`Expected rejection: ${expected}`);
}

(async () => {
  const { api, window, storage } = loadCoreApi();

  await assertRejectsWithMessage(
    api.parseJsonResponse({
      ok: true,
      json: async () => ({ status: 'error', message: 'Permission denied' }),
    }),
    'Permission denied',
  );

  storage.set(api.TOKEN_KEY, 'token');
  await assertRejectsWithMessage(
    api.parseJsonResponse({
      ok: true,
      json: async () => ({ status: 'error', message: 'Not authenticated' }),
    }),
    'Not authenticated',
  );
  assert.strictEqual(storage.get(api.TOKEN_KEY), undefined);
  assert.strictEqual(window.location.href, '/login?next=%2Foperator');
})();
