const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadLoginPage(href = 'https://alarm.example/login') {
  const elements = new Map();
  const document = {
    addEventListener: () => {},
    getElementById: (id) => {
      if (!elements.has(id)) {
        elements.set(id, { addEventListener: () => {}, querySelector: () => ({}) });
      }
      return elements.get(id);
    },
    querySelectorAll: () => [],
  };
  const window = {
    AlarmCoreApi: {
      roleHome: (role) => ({
        operator: '/operator',
        maintenance: '/maintenance',
        supervisor: '/supervisor',
        admin: '/admin',
      }[role] || '/assistant'),
    },
    location: { href, origin: 'https://alarm.example' },
  };
  const context = {
    URL,
    document,
    localStorage: { removeItem: () => {}, setItem: () => {} },
    window,
  };
  context.globalThis = context;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../../static/js/pages/login.js'), 'utf8'),
    context,
    { filename: 'login.js' },
  );
  return window.AlarmLogin;
}

test('login redirect rejects external and protocol-relative destinations', () => {
  const login = loadLoginPage();
  const user = { role: 'admin' };

  assert.equal(login.safeNextPath('https://evil.example/phish', user), '/admin');
  assert.equal(login.safeNextPath('//evil.example/phish', user), '/admin');
  assert.equal(login.safeNextPath('javascript:alert(1)', user), '/admin');
});

test('login redirect permits only same-origin paths allowed for the user role', () => {
  const login = loadLoginPage();

  assert.equal(login.safeNextPath('/admin?tab=users#active', { role: 'admin' }), '/admin?tab=users#active');
  assert.equal(login.safeNextPath('/admin', { role: 'operator' }), '/operator');
  assert.equal(login.safeNextPath('/assistant', { role: 'operator' }), '/assistant');
});
