const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const source = (name) => fs.readFileSync(path.join(root, 'app/static', name), 'utf8');

function runtime(response) {
  const context = vm.createContext({ window: {}, fetch: async () => response });
  vm.runInContext(source('js/shared/api.js'), context);
  return context.window.JobAssistant;
}

test('shared API preserves structured provider errors and validation messages', async () => {
  const cases = [
    [{ code: 'llm_timeout', message: 'raw provider message' }, '模型服务响应超时，请稍后重试或增加超时时间。'],
    [{ code: 'new_error', message: '可读错误' }, '可读错误'],
    [{ code: 'unknown' }, '请求失败（502）'],
    ['plain error', 'plain error'],
    [[{ msg: '字段必填' }, { message: '类型错误' }], '字段必填；类型错误'],
  ];
  for (const [detail, message] of cases) {
    const app = runtime({ ok: false, status: 502, json: async () => ({ detail }) });
    await assert.rejects(app.api('/fixture'), (error) => error.message === message);
  }
});

test('shared API tolerates invalid or null error bodies and empty success responses', async () => {
  for (const json of [async () => { throw new Error('not JSON'); }, async () => null]) {
    const app = runtime({ ok: false, status: 503, json });
    await assert.rejects(app.api('/fixture'), (error) => error.message === '请求失败（503）');
  }
  const empty = runtime({ ok: true, status: 204, json: () => { throw new Error('unexpected parse'); } });
  assert.equal(await empty.api('/fixture'), null);
  const data = { id: 1 };
  assert.equal(await runtime({ ok: true, status: 200, json: async () => data }).api('/fixture'), data);
});

test('common bootstrap loads preferences without page business requests', async () => {
  const calls = [];
  const context = vm.createContext({
    window: {},
    document: { querySelector: () => null, documentElement: { dataset: {} }, addEventListener: () => {} },
    fetch: async (url) => {
      calls.push(url);
      return { ok: true, status: 200, json: async () => [{ key: 'theme', value: 'dark' }] };
    },
  });
  for (const name of ['js/shared/api.js', 'js/shared/dom.js', 'app.js', 'js/pages/tracker.js', 'js/pages/settings.js']) {
    vm.runInContext(source(name), context);
  }
  await context.window.JobAssistant.ready;
  assert.deepEqual(calls, ['/api/settings']);
  assert.equal(context.document.documentElement.dataset.theme, 'dark');
  assert.equal(context.window.preferences.density, 'comfortable');
  assert.equal(typeof context.renderJobs, 'undefined');
  assert.equal(typeof context.loadLLMConfig, 'undefined');
});
