const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const settingsSource = fs.readFileSync(path.join(root, 'app/static/js/workbench/views/settings.js'), 'utf8');
const sourceContains = (value) => settingsSource.includes(value);

let payloads;
test('settings module sends clamped model probe payloads through both probe actions', async () => {
  payloads = await import('../app/static/js/workbench/integrations/settings-payloads.mjs');
  const savedProbe = payloads.modelProbeRequest({ provider: 'openai', api_base: 'https://example.test/v1', timeout_seconds: 60, config_id: 12 });
  assert.equal(savedProbe.method, 'POST');
  assert.deepEqual(JSON.parse(savedProbe.body), {
    provider: 'openai', api_base: 'https://example.test/v1', timeout_seconds: 30, config_id: 12,
  });
  assert.deepEqual(JSON.parse(payloads.modelProbeRequest({ provider: 'openai', api_base: '', timeout_seconds: 15, api_key: 'new-secret', config_id: 12 }).body), {
    provider: 'openai', api_base: 'https://api.openai.com/v1', timeout_seconds: 15, api_key: 'new-secret',
  });
  assert.deepEqual(JSON.parse(payloads.modelProbeRequest({ provider: 'openai', api_base: 'https://example.test/v1', timeout_seconds: 15, api_key: 'new-secret', config_id: 12 }).body), {
    provider: 'openai', api_base: 'https://example.test/v1', timeout_seconds: 15, api_key: 'new-secret',
  });
  assert.match(settingsSource, /const options = modelProbeRequest\(/);
  assert.match(settingsSource, /req\('\/llm\/models-probe', \{ \.\.\.options, signal: probeController.signal \}\)/);
  assert.match(settingsSource, /req\('\/llm\/models-probe', modelProbeRequest\(/);
});

test('empty email keywords are submitted as an empty list without changing the rule', () => {
  const clearRules = payloads.emailFilterRequest('', []);
  assert.equal(clearRules.method, 'PUT');
  assert.deepEqual(JSON.parse(clearRules.body), { regex: null, keywords: [] });
  assert.deepEqual(JSON.parse(payloads.emailFilterRequest('  interview  ', ['校招', '', '实习']).body), { regex: 'interview', keywords: ['校招', '实习'] });
  assert.equal(sourceContains("req('/email-filter', options)"), true);
  assert.equal(sourceContains('const options = emailFilterRequest(regex, keywords)'), true);
  assert.equal(sourceContains("keywords: ['招聘']"), false);
  assert.equal((settingsSource.match(/req\('\/email-filter', options\)/g) || []).length, 1);
});

test('proxy choice is explicit on save and dialog probes, but inherited by saved-model probes', () => {
  const base = { provider: 'openai', config_id: 7 };
  assert.equal('use_proxy' in payloads.buildModelProbePayload(base), false);
  for (const use_proxy of [true, false]) {
    assert.equal(payloads.buildModelSavePayload({ ...base, use_proxy }).use_proxy, use_proxy);
    assert.equal(payloads.buildModelProbePayload({ ...base, use_proxy, api_key: 'replacement' }).use_proxy, use_proxy);
  }
});

test('model API key save preserves a blank credential and supports explicit clearing', () => {
  const base = { display_name: 'main', provider: 'openai', model: 'gpt-x', api_base: '', temperature: 0.2, timeout_seconds: 60, enabled: true };
  const preserve = JSON.parse(payloads.modelSaveRequest({ ...base, api_key: '', clear_api_key: false }, '17').options.body);
  assert.equal('api_key' in preserve, false);
  assert.equal(preserve.clear_api_key, false);
  const clear = JSON.parse(payloads.modelSaveRequest({ ...base, api_key: '', clear_api_key: true }, '17').options.body);
  assert.equal('api_key' in clear, false);
  assert.equal(clear.clear_api_key, true);
  const replaceRequest = payloads.modelSaveRequest({ ...base, api_key: 'replacement', clear_api_key: true }, '17');
  const replace = JSON.parse(replaceRequest.options.body);
  assert.equal(replace.api_key, 'replacement');
  assert.equal(replace.clear_api_key, false);
  assert.equal(replaceRequest.path, '/llm/configs/17');
  assert.equal(replaceRequest.options.method, 'PUT');
  assert.match(settingsSource, /const request = modelSaveRequest\(values, id\)/);
  assert.match(settingsSource, /req\(request\.path, request\.options\)/);
  assert.equal(sourceContains('name="clear_api_key"'), true);
  assert.equal(sourceContains("form.elements.api_key.disabled = event.target.checked"), true);
});

test('reviewed mail actions stay wired to version snapshots, paged inbox and retained-body semantics', () => {
  const mailSource = fs.readFileSync(path.join(root, 'app/static/js/workbench/views/mail.js'), 'utf8');
  assert.match(mailSource, /navigate\(\{ view: 'mail', params, replace: true, refresh: false \}\)/);
  assert.match(mailSource, /email-messages\?limit=\$\{state\.limit\}&offset=\$\{state\.offset\}/);
  assert.match(mailSource, /expected_suggestion_version: version, expected_application_version: applicationVersion/);
  assert.match(mailSource, /field_evidence: sourceExcerpt \? \{ source_excerpt: sourceExcerpt, checked_by: 'user' \}/);
  assert.match(mailSource, /event_type: text\(data\.get\('event_type'\)\), event_round:/);
  assert.match(mailSource, /\['http:', 'https:'\]/);
  assert.match(mailSource, /rel="noopener noreferrer"/);
  assert.match(mailSource, /bodyRetained: source\.body_retained == null \? Boolean\(first\(source\.body_text, source\.original_body_text\)\)/);
  assert.match(mailSource, /const original = email\.bodyRetained \? first\(cleaning\.originalBody, email\.bodyText\) : ''/);
  assert.match(mailSource, /\.\.\.arr\(entry\.links\)/);
  assert.match(mailSource, /state\.detailController\?\.abort\(\)/);
  assert.doesNotMatch(mailSource, /first\(cleaning\.originalBody, email\.bodyPreview\)/);
});
