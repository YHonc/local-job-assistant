const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const backgroundSource = fs.readFileSync(path.join(root, 'extension', 'background.js'), 'utf8');
const PENDING_KEY = 'pendingImportDraftSaveV1';

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return data; },
  };
}

function createHarness(options = {}) {
  const local = {};
  const session = {};
  const calls = [];
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
    alarms: { create() {}, onAlarm: { addListener() {} } },
    runtime: {
      onMessage: { addListener() {} },
      onStartup: { addListener() {} },
      onInstalled: { addListener() {} },
    },
  };
  let uuid = 0;
  const context = vm.createContext({
    chrome,
    crypto: { randomUUID: () => `draft-test-${++uuid}` },
    fetch: async (url, init = {}) => {
      const call = { url: String(url), init, body: init.body ? JSON.parse(init.body) : null };
      calls.push(call);
      if (call.url.endsWith('/api/llm/routing')) return response(200, { max_parallel_calls: 1 });
      if (call.url.endsWith('/api/llm/configs')) return response(200, { configs: [] });
      if (call.url.endsWith('/api/import-drafts')) {
        const draftResponse = options.draftResponse
          ? await options.draftResponse(call, calls.filter((item) => item.url.endsWith('/api/import-drafts')).length)
          : response(201, { id: calls.filter((item) => item.url.endsWith('/api/import-drafts')).length });
        return draftResponse;
      }
      return response(404, {});
    },
    setTimeout,
    clearTimeout,
    AbortController,
    URL,
    Promise,
    Date,
    console,
  });
  vm.runInContext(`${backgroundSource}
globalThis.__draftTest = {
  handleMessage,
  readPendingDraftSaves,
  waitForMutations: () => mutationQueue,
  waitForDraftSaves: () => draftSaveMutationQueue,
};`, context, { filename: 'background.js' });
  return { context, local, session, calls };
}

const capture = (name, overrides = {}) => ({
  url: `https://jobs.example.test/${name}`,
  title: `职位 ${name}`,
  selected_text: `选中 ${name}`,
  text: `正文 ${name}`,
  metadata: { site_name: '示例招聘网' },
  ...overrides,
});

async function command(harness, type, extra = {}) {
  try {
    return await harness.context.__draftTest.handleMessage({ type, ...extra });
  } catch (error) {
    return { ok: false, error: error.message || '扩展后台任务失败。' };
  }
}

async function testNoModelDraftSave() {
  const harness = createHarness();
  const result = await command(harness, 'draft:save', { capture: capture('no-model') });
  assert.equal(result.ok, true);
  assert.equal(result.draft.id, 1);
  const draftCalls = harness.calls.filter((call) => call.url.endsWith('/api/import-drafts'));
  assert.equal(draftCalls.length, 1);
  assert.equal(draftCalls[0].body.source_url, 'https://jobs.example.test/no-model');
  assert.match(draftCalls[0].body.captured_text, /正文 no-model/);
  assert.equal(draftCalls[0].body.source_website, '示例招聘网');
  assert.equal(harness.calls.some((call) => call.url.endsWith('/api/imports/job/extract')), false);
  assert.equal(harness.session[PENDING_KEY], undefined);
}

async function testFailedRetryUsesSameIdempotencyKey() {
  const harness = createHarness({
    draftResponse: async (_call, count) => count === 1
      ? response(503, { detail: { code: 'temporary_failure' } })
      : response(201, { id: 22 }),
  });
  const first = await command(harness, 'draft:save', { capture: capture('retry') });
  assert.equal(first.ok, false);
  assert.equal((await command(harness, 'draft:pending')).count, 1);
  const retry = await command(harness, 'draft:retry');
  assert.equal(retry.ok, true);
  assert.equal(retry.draft.id, 22);
  const draftCalls = harness.calls.filter((call) => call.url.endsWith('/api/import-drafts'));
  assert.equal(draftCalls.length, 2);
  assert.equal(draftCalls[0].body.idempotency_key, draftCalls[1].body.idempotency_key);
  assert.equal(draftCalls[0].body.source_url, draftCalls[1].body.source_url);
  assert.equal(harness.session[PENDING_KEY], undefined);
}

async function testConcurrentFailurePreservesBothCaptures() {
  let releaseFirstFailure;
  let releaseSecondCapture;
  let firstStarted;
  let secondStarted;
  const firstStartedPromise = new Promise((resolve) => { firstStarted = resolve; });
  const secondStartedPromise = new Promise((resolve) => { secondStarted = resolve; });
  const firstGate = new Promise((resolve) => { releaseFirstFailure = resolve; });
  const secondGate = new Promise((resolve) => { releaseSecondCapture = resolve; });
  const harness = createHarness({
    draftResponse: async (_call, count) => {
      if (count === 1) {
        firstStarted();
        await firstGate;
        return response(503, { detail: { code: 'temporary_failure' } });
      }
      if (count === 2) return response(201, { id: 31 });
      if (count === 3) {
        secondStarted();
        await secondGate;
        return response(201, { id: 32 });
      }
      throw new Error(`Unexpected draft request ${count}`);
    },
  });

  const firstSave = command(harness, 'draft:save', { capture: capture('first') });
  await firstStartedPromise;
  const secondSave = command(harness, 'draft:save', { capture: capture('second') });
  releaseFirstFailure();
  await secondStartedPromise;

  // Completing the retried first item must leave the second capture in session.
  const pendingDuringSecondRequest = harness.session[PENDING_KEY];
  assert.equal(Array.isArray(pendingDuringSecondRequest), true);
  assert.equal(pendingDuringSecondRequest.length, 1);
  assert.equal(pendingDuringSecondRequest[0].capture.url, 'https://jobs.example.test/second');

  releaseSecondCapture();
  const [failedFirst, savedSecond] = await Promise.all([firstSave, secondSave]);
  assert.equal(failedFirst.ok, false);
  assert.equal(savedSecond.ok, true);
  assert.equal(savedSecond.draft.id, 32);

  const draftCalls = harness.calls.filter((call) => call.url.endsWith('/api/import-drafts'));
  assert.deepEqual(draftCalls.map((call) => call.body.source_url), [
    'https://jobs.example.test/first',
    'https://jobs.example.test/first',
    'https://jobs.example.test/second',
  ]);
  assert.equal(draftCalls[0].body.idempotency_key, draftCalls[1].body.idempotency_key);
  assert.notEqual(draftCalls[1].body.idempotency_key, draftCalls[2].body.idempotency_key);
  assert.equal(harness.session[PENDING_KEY], undefined);
}

async function testInvalidCaptureDoesNotBlockAndPendingCanBeDiscarded() {
  const harness = createHarness({
    draftResponse: async (_call, count) => count === 1
      ? response(503, { detail: { code: 'temporary_failure' } })
      : response(201, { id: 41 }),
  });
  const invalid = await command(harness, 'draft:save', {
    capture: capture('invalid', { url: 'file:///tmp/job.html' }),
  });
  assert.equal(invalid.ok, false);
  assert.equal(harness.session[PENDING_KEY], undefined);

  const failed = await command(harness, 'draft:save', { capture: capture('discard') });
  assert.equal(failed.ok, false);
  assert.equal((await command(harness, 'draft:pending')).count, 1);
  const discarded = await command(harness, 'draft:discard');
  assert.equal(discarded.ok, true);
  assert.equal(discarded.discardedCount, 1);
  assert.equal((await command(harness, 'draft:pending')).count, 0);

  const saved = await command(harness, 'draft:save', { capture: capture('after-discard') });
  assert.equal(saved.ok, true);
  assert.equal(saved.draft.id, 41);
  assert.equal(harness.session[PENDING_KEY], undefined);
}

async function testLegacySinglePendingRecordCanRetry() {
  const harness = createHarness({ draftResponse: async () => response(201, { id: 51 }) });
  harness.session[PENDING_KEY] = {
    capture: capture('legacy'),
    idempotencyKey: 'legacy-idempotency-key',
    createdAt: Date.now(),
  };
  const retried = await command(harness, 'draft:retry');
  assert.equal(retried.ok, true);
  assert.equal(retried.draft.id, 51);
  const draftCall = harness.calls.find((call) => call.url.endsWith('/api/import-drafts'));
  assert.equal(draftCall.body.idempotency_key, 'legacy-idempotency-key');
  assert.equal(harness.session[PENDING_KEY], undefined);
}

async function testPendingQueueIsCappedWithoutReplacingCaptures() {
  const harness = createHarness({
    draftResponse: async () => response(503, { detail: { code: 'temporary_failure' } }),
  });
  for (let index = 1; index <= 20; index += 1) {
    const result = await command(harness, 'draft:save', { capture: capture(`queued-${index}`) });
    assert.equal(result.ok, false);
  }
  const beforeOverflow = harness.session[PENDING_KEY];
  assert.equal(beforeOverflow.length, 20);
  assert.equal(beforeOverflow[0].capture.url, 'https://jobs.example.test/queued-1');
  assert.equal(beforeOverflow[19].capture.url, 'https://jobs.example.test/queued-20');

  const overflow = await command(harness, 'draft:save', { capture: capture('overflow') });
  assert.equal(overflow.ok, false);
  assert.match(overflow.error, /最多保留 20 份/);
  assert.equal(harness.session[PENDING_KEY].length, 20);
  assert.equal(harness.session[PENDING_KEY].some((item) => item.capture.url.endsWith('/overflow')), false);
}

async function testTelemetryRegression() {
  execFileSync(process.execPath, [path.join(root, 'tests', 'extension-telemetry.test.cjs')], {
    cwd: root,
    stdio: 'pipe',
  });
}

(async () => {
  await testNoModelDraftSave();
  await testFailedRetryUsesSameIdempotencyKey();
  await testConcurrentFailurePreservesBothCaptures();
  await testInvalidCaptureDoesNotBlockAndPendingCanBeDiscarded();
  await testLegacySinglePendingRecordCanRetry();
  await testPendingQueueIsCappedWithoutReplacingCaptures();
  await testTelemetryRegression();
  console.log('extension draft behavior tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
