const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadCoreStorage() {
  const values = new Map();
  const context = {
    window: {},
    localStorage: {
      getItem: (key) => values.get(key) || '',
      setItem: (key, value) => values.set(key, String(value)),
    },
  };
  context.window.localStorage = context.localStorage;
  context.globalThis = context.window;

  const source = fs.readFileSync(path.join(__dirname, '../../static/js/core/storage.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'storage.js' });
  return { storage: context.window.AlarmCoreStorage, values };
}

test('JSON storage round-trips values and uses fallbacks', () => {
  const { storage, values } = loadCoreStorage();

  assert.deepEqual(storage.readStorage('missing', ['fallback']), ['fallback']);
  storage.writeStorage('history', [{ alarm_code: '3000' }]);
  assert.equal(values.get('history'), '[{"alarm_code":"3000"}]');
  assert.equal(storage.readStorage('history', []).length, 1);

  values.set('history', '{broken');
  assert.deepEqual(storage.readStorage('history', []), []);
});

test('stored counters recover from missing and malformed values', () => {
  const { storage, values } = loadCoreStorage();

  storage.incrementStoredCounter('count');
  storage.incrementStoredCounter('count');
  assert.equal(values.get('count'), '2');

  values.set('count', 'not-a-number');
  storage.incrementStoredCounter('count');
  assert.equal(values.get('count'), '1');
});
