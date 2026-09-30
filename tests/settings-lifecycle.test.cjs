const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const read = (name) => fs.readFileSync(path.join(__dirname, '..', 'app/static/js/workbench', name), 'utf8');

test('settings history updates reuse the mounted view and guard only departures', async () => {
  const source = read('bootstrap.js');
  const updates = [];
  let guardCalls = 0;
  const context = vm.createContext({
    root: {}, activeRoute: { view: 'settings' },
    activeCleanup: Object.assign(() => assert.fail('must not destroy settings drafts'), { updateRoute: (route) => updates.push(route) }),
    navigationGuard: () => { guardCalls++; return false; },
    window: {},
    createWorkbenchRouter: (onChange, _browser, guard) => ({ onChange, guard }),
  });
  vm.runInContext(`let router; ${source.slice(source.indexOf('async function showRoute('), source.indexOf('\nfunction registerView('))}\nthis.router = router; this.showRoute = showRoute;`, context);
  const route = { view: 'settings', params: new URLSearchParams('section=processing&settings_panel=email') };
  assert.equal(context.router.guard({ view: 'settings' }, route), true);
  await context.showRoute(route);
  assert.equal(updates[0], route);
  assert.equal(guardCalls, 0);
  assert.equal(context.router.guard(route, { view: 'library' }), false);
  assert.equal(guardCalls, 1);
});

test('an abandoned model probe cannot alter a later dialog or finish its busy indicator', async () => {
  const source = read('views/settings.js');
  const responses = [];
  const feedback = [];
  const button = { busy: false };
  const dropdown = { innerHTML: 'new dialog' };
  const form = { values: { provider: 'openai', api_base: 'https://example.test/v1', timeout_seconds: 15 }, elements: { id: { value: '1' }, use_proxy: { checked: false } } };
  const context = vm.createContext({
    AbortController,
    FormData: class { constructor(f) { this.values = f.values; } get(name) { return this.values[name] ?? ''; } },
    state: { cleaned: false, probeModels: [] },
    q: (selector) => selector.includes('probe-models') ? button : dropdown,
    buttonBusy: (target, value) => { target.busy = value; },
    modelProbeRequest: (payload) => ({ method: 'POST', body: JSON.stringify(payload) }),
    req: (_url, options) => new Promise((resolve) => responses.push({ resolve, options })),
    msg: (_selector, text) => feedback.push(text),
    collection: (value) => Array.isArray(value) ? value : [], esc: String,
  });
  const start = source.indexOf('  function cancelModelProbe(');
  const end = source.indexOf('  async function saveModel(', start);
  vm.runInContext(`let probeSequence = 0, probeController = null; ${source.slice(start, end)}\nthis.probe = modelProbe; this.cancel = cancelModelProbe;`, context);
  const old = context.probe(form);
  context.cancel();
  assert.equal(responses[0].options.signal.aborted, true);
  const current = context.probe(form);
  responses[0].resolve({ models: ['stale-model'] });
  await old;
  assert.equal(dropdown.innerHTML, 'new dialog');
  assert.equal(button.busy, true);
  responses[1].resolve({ models: ['current-model'] });
  await current;
  assert.match(dropdown.innerHTML, /current-model/);
  assert.doesNotMatch(dropdown.innerHTML, /stale-model/);
  assert.equal(button.busy, false);
  assert.equal(feedback.filter((text) => text.includes('服务返回')).length, 1);
});

test('failed saves retain drafts; a successful retry locks edits and accepts the saved baseline', async () => {
  const input = { name: 'value', type: 'text', value: 'saved', disabled: false, matches: () => false };
  const button = { disabled: false };
  const element = { dataset: {}, contains: (target) => target === input, querySelectorAll: (selector) => selector === '[type="submit"]' ? [button] : selector.includes('button') ? [input, button] : [input] };
  const listeners = new Map();
  const root = { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name) };
  const context = vm.createContext({});
  vm.runInContext(read('components/settings-drafts.js').replace('export function', 'function') + '\nthis.create = createSettingsDrafts;', context);
  const drafts = context.create(root);
  drafts.register('sample', element);
  assert.equal(element.inert, true);
  drafts.accept('sample');
  input.value = 'draft';
  listeners.get('input')({ target: input });
  await assert.rejects(drafts.save('sample', async () => { throw new Error('offline'); }), /offline/);
  assert.equal(input.value, 'draft');
  assert.equal(drafts.hasDirty, true);
  assert.equal(input.disabled, false);
  let finish;
  const pending = drafts.save('sample', async () => {
    assert.equal(input.disabled, false, 'payload reads enabled controls');
    await new Promise((resolve) => { finish = resolve; });
    drafts.accept('sample');
  });
  assert.equal(input.disabled, true);
  assert.equal(drafts.busy, true);
  finish();
  await pending;
  assert.equal(drafts.hasDirty, false);
  assert.equal(input.disabled, false);
  assert.equal(button.disabled, true);
  drafts.cleanup();
  assert.equal(listeners.size, 0);
});

test('closing a dirty model editor requires discard and never closes during a save', () => {
  const source = read('views/settings.js');
  const secret = { value: 'fixture-only-secret', type: 'text' };
  let saving = false, discard = false, prompts = 0, cancelled = 0, accepted = 0;
  const dialog = { open: true, dataset: { dialog: 'model' }, close() { this.open = false; }, querySelectorAll: () => [secret] };
  const context = vm.createContext({
    drafts: { saving: () => saving, dirty: () => true, accept: () => { accepted++; } },
    confirm: () => { prompts++; return discard; },
    cancelModelProbe: () => { cancelled++; }, modelReturnFocus: null,
  });
  vm.runInContext(source.slice(source.indexOf('  function hideDialog('), source.indexOf('  function setTab(')) + '\nthis.hide = hideDialog;', context);
  context.hide(dialog);
  assert.equal(dialog.open, true);
  assert.equal(secret.value, 'fixture-only-secret');
  saving = true; discard = true;
  context.hide(dialog);
  assert.equal(dialog.open, true);
  assert.equal(prompts, 1);
  saving = false;
  context.hide(dialog);
  assert.equal(dialog.open, false);
  assert.equal(secret.value, '');
  assert.equal(secret.type, 'password');
  assert.equal(cancelled, 1);
  assert.equal(accepted, 1);
});
