const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');

function loadExports(relativePath, names, globals = {}) {
  let source = fs.readFileSync(path.join(root, relativePath), 'utf8');
  // These UI modules are browser ESM files while the test suite is CommonJS.
  // Strip only import/export syntax and evaluate the actual module body with
  // small dependency stubs; assertions exercise the exported functions.
  source = source.replace(/^import\s+.*?;\s*$/gm, '');
  source = source.replace(/^export\s+/gm, '');
  source += `\nglobalThis.__testedExports = { ${names.join(', ')} };`;
  const context = vm.createContext({ URL, URLSearchParams, ...globals });
  vm.runInContext(source, context, { filename: relativePath });
  return context.__testedExports;
}

const routerExports = () => loadExports(
  'app/static/js/workbench/router.js',
  ['WORKBENCH_VIEWS', 'sanitizeParams', 'parseWorkbenchRoute', 'createWorkbenchRouter'],
);

const editorExports = () => loadExports(
  'app/static/js/workbench/components/job-editor.js',
  ['parseTagsText', 'parseStructuredData', 'buildJobUpdate', 'buildJobCreate', 'createDraftKeyFactory'],
  {
    categoriesFromConfig: () => [],
    escapeHtml: (value) => String(value),
  },
);

class FakeBrowser {
  constructor(url) {
    this.origin = 'http://workbench.test';
    this.entries = [{ state: null, url: this.normalize(url) }];
    this.index = 0;
    this.listeners = new Map();
    this.history = {
      pushState: (state, _title, url) => this.push(state, url),
      replaceState: (state, _title, url) => this.replace(state, url),
      back: () => this.back(),
    };
  }

  normalize(url) {
    const parsed = new URL(url, this.origin);
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }

  get location() {
    const parsed = new URL(this.entries[this.index].url, this.origin);
    return { href: parsed.href, pathname: parsed.pathname, search: parsed.search };
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  push(state, url) {
    this.entries = this.entries.slice(0, this.index + 1);
    this.entries.push({ state, url: this.normalize(url) });
    this.index += 1;
  }

  replace(state, url) {
    this.entries[this.index] = { state, url: this.normalize(url) };
  }

  back() {
    if (this.index === 0) return;
    this.index -= 1;
    for (const listener of this.listeners.get('popstate') || []) listener({ state: this.entries[this.index].state });
  }
}

test('workbench router sanitizes untrusted query fields and preserves repeated locations', () => {
  const { sanitizeParams, parseWorkbenchRoute } = routerExports();
  const params = sanitizeParams(
    'view=mail&job_id=0007&job_id=9&company_id=0&locations=%20%E5%8E%A6%E9%97%A8%20&locations=%E6%B7%B1%E5%9C%B3&locations=&q=%20data%20&limit=999&tab=unknown&needs_review=false&redirect=https%3A%2F%2Fevil.test',
  );

  assert.equal(params.get('job_id'), '7');
  assert.equal(params.has('company_id'), false);
  assert.deepEqual(params.getAll('locations'), ['厦门', '深圳']);
  assert.equal(params.get('q'), 'data');
  assert.equal(params.get('limit'), '100');
  assert.equal(params.has('tab'), false);
  assert.equal(params.get('needs_review'), 'false');
  assert.equal(params.has('redirect'), false);

  const route = parseWorkbenchRoute({ href: '/workbench?view=unknown&locations=%E5%8E%A6%E9%97%A8&locations=%E6%B7%B1%E5%9C%B3&offset=-1' });
  assert.equal(route.view, 'library');
  assert.deepEqual(route.params.getAll('locations'), ['厦门', '深圳']);
  assert.equal(route.params.has('offset'), false);
});

test('router keeps search state through navigation and restores guarded back navigation', () => {
  const { createWorkbenchRouter } = routerExports();
  const browser = new FakeBrowser('/workbench?view=library&q=data&locations=%E5%8E%A6%E9%97%A8&locations=%E6%B7%B1%E5%9C%B3&application_status=%E5%B7%B2%E6%8A%95%E9%80%92&ignored=x');
  const changes = [];
  let allowLeave = true;
  const router = createWorkbenchRouter(
    (route) => changes.push({ view: route.view, href: route.href }),
    browser,
    (previous, next) => allowLeave || next.view !== 'library',
  );

  const follow = router.navigate({ view: 'follow', params: router.route.params });
  assert.equal(follow.view, 'follow');
  assert.equal(browser.location.pathname, '/workbench');
  const followParams = new URLSearchParams(browser.location.search);
  assert.equal(followParams.get('q'), 'data');
  assert.deepEqual(followParams.getAll('locations'), ['厦门', '深圳']);
  assert.equal(followParams.get('application_status'), '已投递');
  assert.equal(followParams.has('ignored'), false);

  allowLeave = false;
  browser.history.back();
  assert.equal(router.route.view, 'follow');
  assert.equal(new URLSearchParams(browser.location.search).get('view'), 'follow');
  assert.equal(changes.length, 1);

  allowLeave = true;
  browser.history.back();
  assert.equal(router.route.view, 'library');
  assert.equal(new URLSearchParams(browser.location.search).get('q'), 'data');
  assert.equal(changes.length, 2);

  router.dispose();
  assert.equal(browser.listeners.get('popstate').size, 0);
});

const editorValues = (overrides = {}) => ({
  company_id: '4',
  title: '数据分析实习生',
  location: '厦门',
  employment_type: '实习',
  job_category: '数据分析',
  category_review_status: 'confirmed',
  tags: 'SQL, 分析',
  organize_status: 'pending',
  salary_text: '15k-20k',
  notes: '岗位备注',
  description: '岗位描述',
  source_url: 'https://jobs.example.test/1',
  source_website: '官网',
  original_title: '原始岗位标题',
  published_at: '2026-09-24',
  structured_data: '{"薪资":"15k-20k","工作地点":"厦门"}',
  ...overrides,
});

test('job creation keeps company, job, and application notes in separate payload fields', () => {
  const { buildJobCreate } = editorExports();
  const body = buildJobCreate({
    companyMode: 'new',
    company: { name: ' 云岚数据 ', aliases: '云岚, YL Data，云岚', notes: '公司备注' },
    values: editorValues({ notes: '岗位备注' }),
    application: { enabled: true, status: '已投递', notes: '投递过程备注' },
    idempotencyKey: 'create-key-1',
  });

  assert.equal(body.company.name, '云岚数据');
  assert.deepEqual(Array.from(body.company.aliases), ['云岚', 'YL Data']);
  assert.equal(body.company.notes, '公司备注');
  assert.equal(body.job.notes, '岗位备注');
  assert.equal(body.application.notes, '投递过程备注');
  assert.equal(body.application.status, '已投递');
  assert.equal(body.idempotency_key, 'create-key-1');
  assert.equal(Object.hasOwn(body.job, 'application_notes'), false);
  assert.equal(Object.hasOwn(body.job, 'company_notes'), false);

  const existingCompanyBody = buildJobCreate({
    companyMode: 'existing', companyId: '12', company: { name: 'ignored', notes: 'ignored' },
    values: editorValues(), application: { enabled: false, notes: 'not saved' }, idempotencyKey: 'create-key-2',
  });
  assert.equal(existingCompanyBody.company_id, 12);
  assert.equal(Object.hasOwn(existingCompanyBody, 'company'), false);
  assert.equal(existingCompanyBody.application, null);
});

test('job updates carry expected versions and keep application edits out of job notes', () => {
  const { buildJobUpdate, parseStructuredData } = editorExports();
  const item = {
    id: 19,
    company_id: 4,
    title: '数据分析实习生',
    location: '厦门',
    employment_type: '实习',
    job_category: '数据分析',
    category_review_status: 'confirmed',
    tags: ['SQL', '分析'],
    organize_status: 'pending',
    salary_text: '15k-20k',
    notes: '旧岗位备注',
    description: '岗位描述',
    source_url: 'https://jobs.example.test/1',
    source_website: '官网',
    original_title: '原始岗位标题',
    published_at: '2026-09-24',
    structured_data: { 薪资: '15k-20k', 工作地点: '厦门' },
    version: 7,
    application: { id: 8, status: '待投递', notes: '旧申请备注', version: 3 },
  };
  const patch = buildJobUpdate(item, editorValues({ salary_text: '', notes: '新岗位备注', structured_data: '{"核验":"人工确认"}' }), {
    status: '已投递', notes: '新申请备注',
  });

  assert.equal(patch.expected_job_version, 7);
  assert.equal(patch.job.salary_text, null);
  assert.equal(patch.job.notes, '新岗位备注');
  assert.deepEqual(JSON.parse(JSON.stringify(patch.job.structured_data)), { 核验: '人工确认' });
  assert.equal(patch.expected_application_version, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(patch.application)), { status: '已投递', notes: '新申请备注' });
  assert.equal(Object.hasOwn(patch.job, 'application_notes'), false);

  const applicationOnly = buildJobUpdate(item, editorValues({ salary_text: '15k-20k', notes: '旧岗位备注', structured_data: '{"薪资":"15k-20k","工作地点":"厦门"}' }), {
    status: '待投递', notes: '只改申请备注',
  });
  assert.equal(Object.hasOwn(applicationOnly, 'job'), false);
  assert.equal(applicationOnly.expected_job_version, 7);
  assert.equal(applicationOnly.expected_application_version, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(applicationOnly.application)), { notes: '只改申请备注' });

  assert.equal(parseStructuredData(''), null);
  assert.deepEqual(JSON.parse(JSON.stringify(parseStructuredData('[{"fact":true}]'))), [{ fact: true }]);
  assert.throws(() => parseStructuredData('{bad json}'), (error) => error.name === 'SyntaxError');
  assert.throws(() => parseStructuredData('"not an object"'), (error) => error.name === 'TypeError');
});

test('draft idempotency keys remain stable for identical retries and change with payload', () => {
  const { createDraftKeyFactory } = editorExports();
  let next = 0;
  const keyFor = createDraftKeyFactory(() => `draft-${++next}`);
  const firstPayload = { company_id: 4, job: { title: '数据分析' }, application: null };
  const retryPayload = JSON.parse(JSON.stringify(firstPayload));
  const changedPayload = { ...firstPayload, job: { title: '算法实习' } };

  const first = keyFor(firstPayload);
  assert.equal(keyFor(retryPayload), first);
  assert.notEqual(keyFor(changedPayload), first);
  assert.equal(next, 2);
});

class FakeEventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatch(type, event) {
    let result;
    for (const listener of this.listeners.get(type) || []) result = listener(event);
    return result;
  }
}

class FakeControl {
  constructor(value = '') { this.value = value; this.checked = false; this.disabled = false; }
  focus() {}
}

class FakeForm extends FakeEventTarget {
  constructor() {
    super();
    this.elements = {
      theme_mode: new FakeControl('light'),
      seed_color: new FakeControl('#5c7f70'),
      density: new FakeControl('comfortable'),
      reduce_motion: new FakeControl(),
      poetry_preset: new FakeControl('random'),
    };
    this.visibleInputs = [];
    this.submitButton = new FakeControl();
  }
  querySelectorAll(selector) {
    if (selector === '[name="visible_job_fields"]:checked') return this.visibleInputs.filter((input) => input.checked);
    return [];
  }
  querySelector(selector) { return selector === '[type="submit"]' ? this.submitButton : null; }
}

class FakeVisibleFields {
  set innerHTML(value) {
    this.inputs = [...value.matchAll(/<input[^>]*name="visible_job_fields"[^>]*value="([^"]+)"([^>]*)>/g)]
      .map((match) => ({ value: match[1], checked: /\bchecked\b/.test(match[2]) }));
    if (this.form) this.form.visibleInputs = this.inputs;
  }
}

class FakeDialog extends FakeEventTarget {
  constructor() {
    super();
    this.open = false;
    this.dataset = {};
    this.status = { textContent: '', dataset: {} };
    this.visibleFields = new FakeVisibleFields();
    this.form = new FakeForm();
    this.visibleFields.form = this.form;
  }
  setAttribute() {}
  set innerHTML(_value) {}
  querySelector(selector) {
    if (selector === '[data-preferences-form]') return this.form;
    if (selector === '[data-preferences-status]') return this.status;
    if (selector === '[data-visible-fields]') return this.visibleFields;
    return null;
  }
  showModal() { this.open = true; }
  close() {
    if (!this.open) return;
    this.open = false;
    this.dispatch('close', {});
  }
  remove() { this.removed = true; }
}

class FakeHintNode {
  constructor() { this.dataset = {}; this.hidden = false; this.disabled = false; this.parent = null; }
  closest(selector) {
    if (selector === '[data-dismiss-workbench-hint]' && Object.hasOwn(this.dataset, 'dismissWorkbenchHint')) return this;
    if (selector === '[data-restore-workbench-hint]' && Object.hasOwn(this.dataset, 'restoreWorkbenchHint')) return this;
    if (selector === '[data-open-workbench-preferences]' && Object.hasOwn(this.dataset, 'openWorkbenchPreferences')) return this;
    if (selector === '[data-workbench-page-hint]') {
      let node = this;
      while (node) {
        if (Object.hasOwn(node.dataset || {}, 'workbenchPageHint')) return node;
        node = node.parent;
      }
    }
    return null;
  }
  querySelector(selector) {
    if (selector === '[data-dismiss-workbench-hint]') return this.dismissButton || null;
    return null;
  }
  after(node) { this.parent.restoreButton = node; node.parent = this.parent; }
  append(node) { node.parent = this; this.dismissButton = node; }
}

class FakeHintRoot {
  constructor(view) {
    this.view = view;
    this.hint = new FakeHintNode();
    this.hint.dataset.workbenchPageHint = view;
    this.hint.parent = this;
    this.hint.dismissButton = new FakeHintNode();
    this.hint.dismissButton.dataset.dismissWorkbenchHint = 'true';
    this.hint.dismissButton.parent = this.hint;
  }
  querySelector(selector) {
    if (selector === '[data-workbench-page-hint]') return this.hint;
    const restore = selector.match(/^\[data-restore-workbench-hint="([^"]+)"\]$/);
    return restore && this.restoreButton?.dataset.restoreWorkbenchHint === restore[1] ? this.restoreButton : null;
  }
}

class FakePreferencesDocument extends FakeEventTarget {
  constructor() {
    super();
    this.activeElement = { isConnected: true, focus() {} };
    this.activeViewRoot = null;
  }
  createElement(tag) { return tag === 'dialog' ? new FakeDialog() : new FakeHintNode(); }
  querySelector(selector) { return selector === '#workbench-view' ? this.activeViewRoot : null; }
}

test('preferences reopen from saved state and persist each page hint independently', async () => {
  const document = new FakePreferencesDocument();
  const rootNode = {
    dataset: {}, style: { setProperty() {} },
    append(dialog) { this.dialog = dialog; },
    querySelectorAll() { return []; },
  };
  const serverPreferences = {
    theme_mode: 'dark', density: 'compact', seed_color: '#234567', reduce_motion: false,
    visible_job_fields: ['category', 'application'], dismissed_page_hints: { pending: false, follow: false, mail: false },
    poetry_preset: 'random',
  };
  const requests = [];
  const api = async (_url, options) => {
    if (!options) return structuredClone(serverPreferences);
    const patch = JSON.parse(options.body);
    requests.push(patch);
    if (patch.dismissed_page_hints) {
      serverPreferences.dismissed_page_hints = { ...serverPreferences.dismissed_page_hints, ...patch.dismissed_page_hints };
    }
    const { dismissed_page_hints: _hintPatch, ...scalarPatch } = patch;
    Object.assign(serverPreferences, scalarPatch);
    return structuredClone(serverPreferences);
  };
  const window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }) };
  const { createPreferencesManager } = loadExports(
    'app/static/js/workbench/components/preferences.js',
    ['createPreferencesManager'],
    { document, window },
  );
  const manager = createPreferencesManager({ root: rootNode, api });
  await manager.ready;

  manager.open();
  const dialog = rootNode.dialog;
  const form = dialog.form;
  assert.equal(form.elements.theme_mode.value, 'dark');
  assert.equal(form.elements.density.value, 'compact');
  assert.deepEqual(form.visibleInputs.filter((input) => input.checked).map((input) => input.value), ['category', 'application']);

  form.elements.theme_mode.value = 'light';
  manager.open();
  assert.equal(form.elements.theme_mode.value, 'dark', 'opening again discards unsaved control edits');

  form.elements.theme_mode.value = 'system';
  form.elements.seed_color.value = '#112233';
  form.elements.density.value = 'comfortable';
  form.elements.reduce_motion.checked = true;
  form.elements.poetry_preset.value = 'nian';
  form.visibleInputs.forEach((input) => { input.checked = input.value === 'tags'; });
  await form.dispatch('submit', { preventDefault() {} });
  assert.equal(manager.value.theme_mode, 'system');
  assert.equal(manager.value.seed_color, '#112233');
  assert.equal(manager.value.density, 'comfortable');
  assert.equal(manager.value.reduce_motion, true);
  assert.deepEqual(Array.from(manager.value.visible_job_fields), ['tags']);
  assert.equal(rootNode.dataset.themeMode, 'system');
  assert.equal(rootNode.dataset.reduceMotion, 'true');

  manager.open();
  assert.equal(form.elements.theme_mode.value, 'system');
  assert.equal(form.elements.seed_color.value, '#112233');
  assert.deepEqual(form.visibleInputs.filter((input) => input.checked).map((input) => input.value), ['tags']);
  dialog.close();

  const views = ['pending', 'follow', 'mail'];
  const hintRoots = new Map(views.map((view) => [view, new FakeHintRoot(view)]));
  for (const view of views) manager.bindPageHints(hintRoots.get(view), view);
  for (const view of views) {
    const viewRoot = hintRoots.get(view);
    document.activeViewRoot = viewRoot;
    await document.dispatch('click', { target: viewRoot.hint.dismissButton });
    assert.equal(manager.value.dismissed_page_hints[view], true);
    assert.equal(viewRoot.hint.hidden, true);
    assert.equal(viewRoot.restoreButton.hidden, false);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(manager.value.dismissed_page_hints)), { pending: true, follow: true, mail: true });
  assert.deepEqual(requests.slice(-3), views.map((view) => ({ dismissed_page_hints: { [view]: true } })));

  const followRoot = hintRoots.get('follow');
  document.activeViewRoot = followRoot;
  await document.dispatch('click', { target: followRoot.restoreButton });
  assert.deepEqual(JSON.parse(JSON.stringify(manager.value.dismissed_page_hints)), { pending: true, follow: false, mail: true });
  assert.equal(followRoot.hint.hidden, false);
  assert.equal(followRoot.restoreButton.hidden, true);
  assert.deepEqual(requests.at(-1), { dismissed_page_hints: { follow: false } });

  manager.dispose();
  assert.equal(document.listeners.get('click').size, 0);
});
