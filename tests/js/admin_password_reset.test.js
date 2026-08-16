const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadAdminPage(user) {
  const calls = [];
  const state = new Map([
    ['adminUsers', [user]],
    ['adminSessions', []],
  ]);
  const elements = new Map();
  const app = {
    $: (id) => {
      if (!elements.has(id)) {
        elements.set(id, { innerHTML: '', textContent: '', value: '' });
      }
      return elements.get(id);
    },
    apiJson: async (requestPath, options) => {
      calls.push({ path: requestPath, options });
      return requestPath === '/sessions'
        ? { status: 'ok', sessions: [] }
        : { status: 'ok' };
    },
    formatError: (error) => error.message,
    getState: (key) => state.get(key),
    setResultMessage: (element, className, message) => {
      element.className = className;
      element.textContent = message;
    },
    setState: (key, value) => state.set(key, value),
  };
  const document = {
    addEventListener: () => {},
    createElement: () => ({ click: () => {} }),
    querySelectorAll: () => [],
  };
  const window = {
    AlarmApp: app,
    addEventListener: () => {},
    location: { hash: '' },
    prompt: () => 'strong-password',
  };
  const context = {
    Blob,
    URL,
    document,
    encodeURIComponent,
    window,
  };
  context.globalThis = context;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../../static/js/pages/admin.js'), 'utf8'),
    context,
    { filename: 'admin.js' },
  );
  return { calls, window };
}

test('admin password reset submits the displayed user version and shows success', async () => {
  const { calls, window } = loadAdminPage({
    user_id: 'operator01',
    updated_at: '2026-08-14T08:00:00+00:00',
  });

  await window.resetAdminPassword('operator01');

  assert.equal(calls[0].path, '/users/operator01/password');
  assert.deepEqual(
    JSON.parse(calls[0].options.body),
    {
      password: 'strong-password',
      expected_updated_at: '2026-08-14T08:00:00+00:00',
    },
  );
  assert.equal(window.AlarmApp.$('adminUserResult').textContent, 'operator01 密碼已重設，既有 Session 已撤銷');
  assert.equal(window.AlarmApp.$('adminUserResult').className, 'upload-result show');
});

test('legacy users without a version can still reset their password', async () => {
  const { calls, window } = loadAdminPage({ user_id: 'operator01' });

  await window.resetAdminPassword('operator01');

  assert.deepEqual(JSON.parse(calls[0].options.body), { password: 'strong-password' });
});
