const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadOperatorPage() {
  const context = {
    window: {
      AlarmApp: {
        esc: (value) => String(value)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;'),
      },
    },
    document: { addEventListener: () => {} },
  };
  context.globalThis = context;
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/pages/operator.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'operator.js' });
  return context;
}

test('long operator suggestions keep the complete text and can be collapsed', () => {
  const page = loadOperatorPage();
  const fullText = `${'Complete maintenance guidance. '.repeat(30)}FINAL STEP`;

  const html = page.renderSuggestionSection('建議處置', fullText, 'primary');

  assert.match(html, /<details class="suggestion-details" open>/);
  assert.match(html, /FINAL STEP/);
  assert.doesNotMatch(html, /FINAL STEP\.\.\./);
  assert.equal(page.fullSuggestionText(fullText).endsWith('FINAL STEP'), true);
});

test('short operator suggestions render fully without an expand control', () => {
  const page = loadOperatorPage();
  const html = page.renderSuggestionSection('建議處置', 'Check the cable and restart.', 'primary');

  assert.doesNotMatch(html, /<details/);
  assert.match(html, /Check the cable and restart\./);
});
