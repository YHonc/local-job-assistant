const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const backgroundSource = fs.readFileSync(path.join(root, 'extension', 'background.js'), 'utf8');
const popupSource = fs.readFileSync(path.join(root, 'extension', 'popup.js'), 'utf8');

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

function createHarness() {
  const local = {};
  const session = {};
  const area = (store) => ({
    async get(key) {
      if (typeof key === 'string') return { [key]: clone(store[key]) };
      return clone(store);
    },
    async set(values) {
      Object.assign(store, clone(values));
    },
    async remove(key) {
      for (const name of (Array.isArray(key) ? key : [key])) delete store[name];
    },
  });
  const chrome = {
    storage: {
      local: area(local),
      session: area(session),
      onChanged: { addListener() {} },
    },
    alarms: {
      create() {},
      onAlarm: { addListener() {} },
    },
    runtime: {
      onMessage: { addListener() {} },
      onStartup: { addListener() {} },
      onInstalled: { addListener() {} },
    },
  };
  const context = vm.createContext({
    chrome,
    crypto: { randomUUID: () => 'test-task-id' },
    fetch: async (url) => ({
      ok: true,
      status: 200,
      async json() {
        return String(url).endsWith('/routing')
          ? { max_parallel_calls: 2, auto_select_enabled: true, default_config_id: null }
          : { configs: [] };
      },
    }),
    setTimeout,
    clearTimeout,
    AbortController,
    Promise,
    Date,
    console,
  });
  vm.runInContext(`${backgroundSource}
globalThis.__extensionTest = {
  claimExtract,
  finishExtract,
  failExtract,
  retryTask,
  waitForMutations: () => mutationQueue,
};`, context, { filename: 'background.js' });
  return { context, local, session };
}

function task(id, createdAt, overrides = {}) {
  return {
    id,
    status: 'queued',
    sourceTitle: id,
    sourceUrl: `https://example.test/${id}`,
    selected: false,
    triedModelIds: [],
    failoverCount: 0,
    createdAt,
    updatedAt: createdAt,
    ...overrides,
  };
}

const models = [
  { id: 1, display_name: '主模型', provider: 'openai-compatible', model: 'model-a', enabled: true },
  { id: 2, display_name: '备用模型', provider: 'openai-compatible', model: 'model-b', enabled: true },
];

async function readTasks(harness) {
  await harness.context.__extensionTest.waitForMutations();
  return harness.local.jobImportTasksV2 || [];
}

async function testParallelMetadataIsolation() {
  const harness = createHarness();
  const now = Date.now();
  harness.local.jobImportTasksV2 = [task('a', now - 2), task('b', now - 1)];
  const [first, second] = await Promise.all([
    harness.context.__extensionTest.claimExtract(models, null),
    harness.context.__extensionTest.claimExtract(models, null),
  ]);
  const stored = await readTasks(harness);
  assert.notEqual(first.id, second.id);
  assert.deepEqual(new Set(stored.map((item) => item.id)), new Set(['a', 'b']));
  assert.equal(stored.find((item) => item.id === first.id).attemptCount, 1);
  assert.equal(stored.find((item) => item.id === second.id).attemptCount, 1);
  assert.notDeepEqual(
    stored.find((item) => item.id === first.id).modelSnapshot,
    stored.find((item) => item.id === second.id).modelSnapshot,
  );
  assert.ok(stored.every((item) => Number.isFinite(item.extractStartedAt)));
  await Promise.all([
    harness.context.__extensionTest.finishExtract(first, {
      draft: { title: '第一个' },
      duplicates: [],
      model_config_id: 1,
      telemetry: {
        model: { config_id: 1, display_name: '主模型', provider: 'p', model: 'a' },
        prompt_tokens: 11,
        completion_tokens: 0,
        duration_ms: 10,
        provider_calls: 1,
        usage_complete: true,
      },
    }),
    harness.context.__extensionTest.finishExtract(second, {
      draft: { title: '第二个' },
      duplicates: [],
      model_config_id: 2,
      telemetry: {
        model: { config_id: 2, display_name: '备用模型', provider: 'p', model: 'b' },
        prompt_tokens: 22,
        completion_tokens: 2,
        duration_ms: 20,
        provider_calls: 2,
        usage_complete: false,
      },
    }),
  ]);
  const completed = await readTasks(harness);
  assert.equal(completed.find((item) => item.id === first.id).telemetry.prompt_tokens, 11);
  assert.equal(completed.find((item) => item.id === second.id).telemetry.prompt_tokens, 22);
  assert.equal(completed.find((item) => item.id === second.id).telemetry.usage_complete, false);
}

async function testRetryClearsOldTelemetryAndKeepsAttempts() {
  const harness = createHarness();
  const now = Date.now();
  harness.local.jobImportTasksV2 = [task('retry-me', now, {
    status: 'failed',
    modelConfigId: 1,
    modelSnapshot: { config_id: 1, display_name: '旧模型', provider: 'old', model: 'old' },
    telemetry: {
      model: { config_id: 1, display_name: '旧模型', provider: 'old', model: 'old' },
      prompt_tokens: 17,
      completion_tokens: 9,
      duration_ms: 10,
      provider_calls: 1,
      usage_complete: true,
    },
    attemptCount: 2,
    extractStartedAt: now - 100,
    extractFinishedAt: now,
    error: '失败',
  })];
  harness.session['jobImportCapture:retry-me'] = { url: 'https://example.test/retry-me', title: '重试' };
  const retried = await harness.context.__extensionTest.retryTask('retry-me');
  assert.equal(retried.status, 'queued');
  assert.equal(retried.attemptCount, 2);
  assert.equal(retried.telemetry, null);
  assert.equal(retried.extractStartedAt, null);
  assert.equal(retried.extractFinishedAt, null);
  assert.equal(retried.modelSnapshot, null);
  const claimed = await harness.context.__extensionTest.claimExtract(models, null);
  assert.equal(claimed.attemptCount, 3);
  assert.equal(claimed.telemetry, null);
  assert.ok(Number.isFinite(claimed.extractStartedAt));
  await harness.context.__extensionTest.failExtract(claimed, { name: 'AbortError' });
  const failover = (await readTasks(harness)).find((item) => item.id === 'retry-me');
  assert.equal(failover.status, 'queued');
  assert.equal(failover.telemetry, null);
  assert.equal(failover.modelSnapshot, null);
  assert.ok(Number.isFinite(failover.extractFinishedAt));
}

async function testTimingAndZeroTokens() {
  const harness = createHarness();
  const now = Date.now();
  harness.local.jobImportTasksV2 = [task('zero', now)];
  const claimed = await harness.context.__extensionTest.claimExtract(models, null);
  await harness.context.__extensionTest.finishExtract(claimed, {
    draft: { title: '合法 0' },
    duplicates: [],
    model_config_id: 1,
    telemetry: {
      model: { config_id: 1, display_name: '主模型', provider: 'openai-compatible', model: 'model-a', api_key: 'must-not-persist' },
      prompt_tokens: 0,
      completion_tokens: 0,
      duration_ms: 0,
      provider_calls: 1,
      usage_complete: true,
    },
  });
  const stored = (await readTasks(harness)).find((item) => item.id === 'zero');
  assert.equal(stored.status, 'review');
  assert.equal(stored.telemetry.prompt_tokens, 0);
  assert.equal(stored.telemetry.completion_tokens, 0);
  assert.ok(Number.isFinite(stored.extractStartedAt));
  assert.ok(Number.isFinite(stored.extractFinishedAt));
  assert.ok(stored.extractFinishedAt >= stored.extractStartedAt);
  assert.equal(Object.hasOwn(stored.telemetry.model, 'api_key'), false);
}

async function testLegacyResponseModelMismatch() {
  const harness = createHarness();
  await harness.context.__extensionTest.waitForMutations();
  const now = Date.now();
  harness.local.jobImportTasksV2 = [task('legacy', now, {
    status: 'extracting',
    modelConfigId: 1,
    modelSnapshot: { config_id: 1, display_name: '旧模型', provider: 'old', model: 'old' },
    attemptCount: 1,
    extractStartedAt: now - 10,
  })];
  await harness.context.__extensionTest.finishExtract(
    { id: 'legacy' },
    { draft: { title: '旧接口' }, duplicates: [], model_config_id: 2 },
  );
  const stored = (await readTasks(harness)).find((item) => item.id === 'legacy');
  assert.equal(stored.modelConfigId, 2);
  assert.equal(stored.modelSnapshot, null);
  assert.equal(stored.telemetry, null);
}

function createPopupHarness() {
  const elements = new Map();
  const element = () => ({
    addEventListener() {},
    append() {},
    appendChild() {},
    replaceChildren() {},
    focus() {},
    setAttribute() {},
    removeEventListener() {},
    classList: { add() {} },
    dataset: {},
    style: {},
    hidden: false,
    disabled: false,
    checked: false,
    indeterminate: false,
    value: '',
    textContent: '',
  });
  const document = {
    body: { classList: { add() {} } },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement: element,
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({
    document,
    location: { search: '?preview' },
    window: { PreviewAPI: {
      subscribe() {},
      async command() { return { ok: true, tasks: [] }; },
      capture() { return {}; },
    } },
    structuredClone,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval() { return 0; },
    console,
  });
  const exportsSource = '\nglobalThis.__popupTest = { taskTimestamp, taskDurationMs, formatTaskDuration, formatToken, formatAttempts };';
  vm.runInContext(popupSource + exportsSource, context, { filename: 'popup.js' });
  return context.__popupTest;
}

async function testPopupTelemetryBoundaries() {
  const popup = createPopupHarness();
  assert.equal(popup.taskTimestamp(null), null);
  assert.equal(popup.taskTimestamp(''), null);
  assert.equal(popup.formatToken(0), '0');
  assert.equal(popup.formatToken(-1), '未返回');
  assert.equal(popup.formatTaskDuration({ status: 'queued', extractStartedAt: null }), '未开始');
  assert.equal(popup.formatTaskDuration({ status: 'failed', extractStartedAt: 100 }), '未记录');
  assert.equal(popup.formatTaskDuration({ status: 'extracting', extractStartedAt: 100 }, 1500), '1秒');
  assert.equal(popup.formatAttempts({ status: 'failed', failoverCount: 2 }), '未记录');
}

(async () => {
  await testParallelMetadataIsolation();
  await testRetryClearsOldTelemetryAndKeepsAttempts();
  await testTimingAndZeroTokens();
  await testLegacyResponseModelMismatch();
  await testPopupTelemetryBoundaries();
  console.log('extension telemetry behavior tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
