const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const BASE_URL = 'http://127.0.0.1:18769';
const ORIGIN = new URL(BASE_URL).origin;
const OUTPUT_DIR = path.resolve(__dirname, '../../output/playwright/workbench');
const REPORT_PATH = path.join(OUTPUT_DIR, 'integrations-acceptance.json');
const SENSITIVE_KEY = /api[_-]?key|authorization[_-]?code|push[_-]?url|secret|password|token|body_text|field_evidence/i;

function redact(value, key = '') {
  if (SENSITIVE_KEY.test(key)) return '[REDACTED]';
  if (Array.isArray(value)) return value.map((item) => redact(item));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([childKey, child]) => [childKey, redact(child, childKey)]));
  }
  return value;
}

function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return [];
}

function assertThat(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const playwrightRuntime = process.argv[2];
  if (!playwrightRuntime) throw new Error('用法：node scripts/dev/check-workbench-integrations.cjs <playwright-core目录>');
  const { chromium } = require(path.resolve(playwrightRuntime));
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const report = {
    baseURL: BASE_URL,
    isolation: 'synthetic preview only; browser requests are restricted to 127.0.0.1:18769',
    checks: [],
    requests: [],
    expectedHttpConsoleErrors: [],
    consoleErrors: [],
    pageErrors: [],
    blockedExternalRequests: [],
  };
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let page;
  let context;
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    page = await context.newPage();
    const requestRecords = new WeakMap();
    let expectedFilter422Console = false;
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.origin !== ORIGIN) {
        report.blockedExternalRequests.push(`${url.origin}${url.pathname}`);
        return;
      }
      if (!url.pathname.startsWith('/api/')) return;
      let body = null;
      try { body = request.postDataJSON(); } catch { body = request.postData() || null; }
      const record = { method: request.method(), path: `${url.pathname}${url.search}`, body: redact(body), status: null };
      report.requests.push(record);
      requestRecords.set(request, record);
    });
    page.on('response', (response) => {
      const record = requestRecords.get(response.request());
      if (record) record.status = response.status();
      const url = new URL(response.url());
      if (url.origin === ORIGIN && url.pathname === '/api/email-filter' && response.request().method() === 'PUT' && response.status() === 422) {
        expectedFilter422Console = true;
        report.expectedHttpConsoleErrors.push({ method: 'PUT', path: '/api/email-filter', status: 422, consoleMessage: null });
      }
    });
    page.on('console', (message) => {
      if (message.type() !== 'error') return;
      const expected = report.expectedHttpConsoleErrors.find((item) => item.consoleMessage === null);
      if (expectedFilter422Console && expected && /\b422\b/.test(message.text())) {
        expected.consoleMessage = redact(message.text());
        expectedFilter422Console = false;
      } else {
        report.consoleErrors.push(redact(message.text()));
      }
    });
    page.on('pageerror', (error) => report.pageErrors.push(redact(error.message)));
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== ORIGIN) {
        report.blockedExternalRequests.push(`${url.origin}${url.pathname}`);
        await route.abort();
        return;
      }
      await route.continue();
    });

    const apiGet = async (resource) => {
      if (!resource.startsWith('/')) throw new Error(`API路径必须以/开头：${resource}`);
      const response = await page.request.get(`${BASE_URL}/api${resource}`);
      let data;
      try { data = await response.json(); } catch { data = null; }
      if (!response.ok()) throw new Error(`GET ${resource}失败，HTTP ${response.status()}`);
      return data;
    };
    const expectResponse = async (locator, method, pathname, { allowFailure = false } = {}) => {
      const wait = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.origin === ORIGIN && url.pathname === `/api${pathname}` && response.request().method() === method;
      }, { timeout: 20000 });
      await locator.click();
      const response = await wait;
      let data;
      try { data = await response.json(); } catch { data = null; }
      if (!allowFailure && !response.ok()) throw new Error(`${method} ${pathname}失败，HTTP ${response.status()}`);
      return { response, data, request: response.request() };
    };
    const note = (name, detail = {}) => report.checks.push({ name, ...redact(detail) });

    await page.goto(`${BASE_URL}/workbench?view=settings`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-settings-view]').waitFor({ timeout: 20000 });
    await page.locator('[data-model-cards]').waitFor();
    await page.locator('[data-model-feedback]').waitFor();

    // Exercise an ephemeral model config entirely through the UI and fake API.
    await page.locator('[data-action="new-model"]').click();
    const modelDialog = page.locator('dialog[data-dialog="model"]');
    await modelDialog.locator('[name="display_name"]').fill('集成验收合成模型');
    await modelDialog.locator('[name="provider"]').selectOption('openai');
    await modelDialog.locator('[name="model"]').fill('preview-model-a');
    await modelDialog.locator('[name="api_base"]').fill('http://127.0.0.1:1/v1');
    await modelDialog.locator('[name="timeout_seconds"]').fill('90');
    await modelDialog.locator('[name="api_key"]').fill('synthetic-only-acceptance-key');
    const probe = await expectResponse(modelDialog.locator('[data-action="probe-models"]'), 'POST', '/llm/models-probe');
    const probeRequest = probe.request.postDataJSON();
    assertThat(probeRequest.timeout_seconds === 30, '模型探测超时未按输入90秒限定到30秒');
    assertThat(probeRequest.timeout_seconds >= 3 && probeRequest.timeout_seconds <= 30, '模型探测超出服务端3–30秒范围');
    assertThat(asArray(probe.data?.models).some((model) => (typeof model === 'string' ? model : model.id) === 'preview-model-a'), '服务端fake模型探测结果未显示在界面');
    await modelDialog.locator('[name="probed_model"]').selectOption('preview-model-a');
    const created = await expectResponse(modelDialog.locator('[type="submit"]'), 'POST', '/llm/configs');
    const createdModel = created.data;
    assertThat(Boolean(createdModel?.id), '模型保存响应缺少配置ID');
    const createBody = created.request.postDataJSON();
    assertThat(createBody.clear_api_key === false, '新模型请求未明确保留默认凭据语义');
    assertThat(createBody.api_key === 'synthetic-only-acceptance-key', '新模型请求未提交合成密钥字段');
    await page.locator(`[data-model-card="${createdModel.id}"]`).waitFor();
    const modelListAfterCreate = asArray(await apiGet('/llm/configs'));
    const savedModel = modelListAfterCreate.find((item) => Number(item.id) === Number(createdModel.id));
    assertThat(Boolean(savedModel && savedModel.has_api_key && !('api_key' in savedModel)), '模型服务端结果未安全保存凭据标记');
    note('model-create-and-probe', { id: createdModel.id, provider: savedModel.provider, model: savedModel.model, timeout_seconds: probeRequest.timeout_seconds, has_api_key: savedModel.has_api_key });

    await expectResponse(page.locator(`[data-model-test="${createdModel.id}"]`), 'POST', `/llm/configs/${createdModel.id}/test`);
    await page.locator('[data-model-feedback]').filter({ hasText: '连接成功' }).waitFor();
    await expectResponse(page.locator(`[data-model-probe="${createdModel.id}"]`), 'POST', '/llm/models-probe');
    const savedProbeCall = [...report.requests].reverse().find((item) => item.path === '/api/llm/models-probe' && item.method === 'POST');
    assertThat(Boolean(savedProbeCall && Number(savedProbeCall.body.config_id) === Number(createdModel.id)), '已保存模型探测没有复用本地配置ID');
    assertThat(savedProbeCall.body.timeout_seconds >= 3 && savedProbeCall.body.timeout_seconds <= 30, '已保存模型探测超出3–30秒范围');

    await expectResponse(page.locator(`[data-model-usage="${createdModel.id}"]`), 'GET', `/llm/configs/${createdModel.id}/usage`);
    // The click handler starts the usage GET; depending on URLSearchParams order the predicate above is path-only.
    await page.locator('dialog[data-dialog="usage"][open]').waitFor();
    await page.locator('[data-usage-feedback]').filter({ hasText: '按' }).waitFor({ timeout: 20000 });
    const usageResponse = [...report.requests].reverse().find((item) => item.method === 'GET' && item.path.startsWith(`/api/llm/configs/${createdModel.id}/usage?`));
    assertThat(Boolean(usageResponse && usageResponse.status === 200), '用量趋势请求未成功');
    await page.locator('dialog[data-dialog="usage"] [data-close]').click();

    const beforeToggle = asArray(await apiGet('/llm/configs')).find((item) => Number(item.id) === Number(createdModel.id));
    assertThat(Boolean(beforeToggle?.enabled), '新建模型预期为启用状态');
    const disabled = await expectResponse(page.locator(`[data-model-toggle="${createdModel.id}"]`), 'PUT', `/llm/configs/${createdModel.id}`);
    assertThat(disabled.request.postDataJSON().enabled === false, '停用操作提交了错误状态');
    assertThat(asArray(await apiGet('/llm/configs')).find((item) => Number(item.id) === Number(createdModel.id))?.enabled === false, '模型停用未持久化');
    const enabled = await expectResponse(page.locator(`[data-model-toggle="${createdModel.id}"]`), 'PUT', `/llm/configs/${createdModel.id}`);
    assertThat(enabled.request.postDataJSON().enabled === true, '启用操作提交了错误状态');
    assertThat(asArray(await apiGet('/llm/configs')).find((item) => Number(item.id) === Number(createdModel.id))?.enabled === true, '模型启用未持久化');

    const routing = page.locator('[data-form="routing"]');
    await routing.locator('[name="default_config_id"]').selectOption(String(createdModel.id));
    await routing.locator('[name="email_config_id"]').selectOption(String(createdModel.id));
    await routing.locator('[name="auto_select_enabled"]').check();
    await routing.locator('[name="max_parallel_calls"]').fill('2');
    const routingSave = await expectResponse(routing.locator('[type="submit"]'), 'PUT', '/llm/routing');
    assertThat(Number(routingSave.request.postDataJSON().default_config_id) === Number(createdModel.id), '模型路由默认模型请求错误');
    const savedRouting = await apiGet('/llm/routing');
    assertThat(Number(savedRouting.default_config_id) === Number(createdModel.id) && Number(savedRouting.email_config_id) === Number(createdModel.id) && Number(savedRouting.max_parallel_calls) === 2, '模型路由未持久化');
    note('model-test-usage-toggle-routing', { id: createdModel.id, enabled: true, default_config_id: savedRouting.default_config_id, max_parallel_calls: savedRouting.max_parallel_calls, usageStatus: usageResponse.status });

    // Editing a credential-bearing config must keep blank=preserve and expose a separate clear action.
    await page.locator(`[data-model-edit="${createdModel.id}"]`).click();
    const editDialog = page.locator('dialog[data-dialog="model"]');
    const clearKey = editDialog.locator('[name="clear_api_key"]');
    await clearKey.waitFor();
    assertThat(!(await clearKey.isDisabled()), '有已保存密钥的模型没有启用清除操作');
    await clearKey.check();
    assertThat(await editDialog.locator('[name="api_key"]').isDisabled(), '清除密钥勾选后API Key输入仍可用');
    const clearResponse = await expectResponse(editDialog.locator('[type="submit"]'), 'PUT', `/llm/configs/${createdModel.id}`);
    const clearBody = clearResponse.request.postDataJSON();
    assertThat(clearBody.clear_api_key === true && !('api_key' in clearBody), '显式清除密钥请求字段错误');
    const clearedModel = asArray(await apiGet('/llm/configs')).find((item) => Number(item.id) === Number(createdModel.id));
    assertThat(Boolean(clearedModel && clearedModel.has_api_key === false && !('api_key' in clearedModel)), '显式清除后服务端仍报告已保存密钥');
    note('model-clear-key', { id: createdModel.id, has_api_key: clearedModel.has_api_key, clear_api_key: clearBody.clear_api_key });
    page.once('dialog', (dialog) => dialog.accept());
    await expectResponse(page.locator(`[data-model-delete="${createdModel.id}"]`), 'DELETE', `/llm/configs/${createdModel.id}`);
    assertThat(!asArray(await apiGet('/llm/configs')).some((item) => Number(item.id) === Number(createdModel.id)), '模型删除没有从服务端配置中移除');
    note('model-delete', { id: createdModel.id, deleted: true });

    // Account edits omit blank credentials, filter failure preserves the typed rules, and preview is real API behavior.
    await page.locator('[data-setting-tab="mailbox"]').click();
    await page.locator('[data-setting-panel="mailbox"]').waitFor({ state: 'visible' });
    await page.locator('[data-setting-panel="mailbox"] details').evaluate((details) => { details.open = true; });
    const accounts = asArray(await apiGet('/email-accounts'));
    assertThat(accounts.length > 0, 'fake服务未提供招聘邮箱账户');
    const account = accounts[0];
    await page.locator(`[data-account-edit="${account.id}"]`).click();
    const accountDialog = page.locator('dialog[data-dialog="account"]');
    await accountDialog.locator('[name="check_interval_minutes"]').fill('30');
    const accountEdit = await expectResponse(accountDialog.locator('[type="submit"]'), 'PATCH', `/email-accounts/${account.id}`);
    assertThat(!('authorization_code' in accountEdit.request.postDataJSON()), '邮箱编辑时空授权码没有保持省略');
    const savedAccount = asArray(await apiGet('/email-accounts')).find((item) => Number(item.id) === Number(account.id));
    assertThat(Number(savedAccount?.check_interval_minutes) === 30, '邮箱账户参数未持久化');

    const oldFilter = await apiGet('/email-filter');
    const typedRegex = '面试|笔试|投递';
    await page.locator('[data-filter-regex]').fill(typedRegex);
    await page.locator('[data-filter-keywords]').fill('');
    const emptyFilter = await expectResponse(page.locator('[data-action="save-filter"]'), 'PUT', '/email-filter', { allowFailure: true });
    assertThat(emptyFilter.response.status() === 422, 'fixture没有触发预期的422空关键词保存失败场景');
    await page.locator('[data-filter-feedback]').filter({ hasText: '保存失败' }).waitFor();
    assertThat(await page.locator('[data-filter-regex]').inputValue() === typedRegex && await page.locator('[data-filter-keywords]').inputValue() === '', '失败后筛选表单草稿被覆盖');
    const filterCalls = report.requests.filter((item) => item.path === '/api/email-filter' && item.method === 'PUT');
    assertThat(filterCalls.length === 1 && JSON.stringify(filterCalls[0].body?.keywords) === '[]', '空规则失败后发生了隐式重试或改写');
    const filterAfterFailure = await apiGet('/email-filter');
    assertThat(JSON.stringify(filterAfterFailure) === JSON.stringify(oldFilter), '空规则保存失败却修改了服务端预筛设置');
    await page.locator('[data-filter-sample]').fill('数据分析岗位邀请面试，请确认时间。');
    const preview = await expectResponse(page.locator('[data-action="preview-filter"]'), 'POST', '/email-filter/preview');
    assertThat(preview.response.ok(), '邮件预筛预览API请求失败');
    await page.locator('[data-preview-feedback]').filter({ hasText: '命中' }).waitFor();
    note('mailbox-filter-failure-preview', { accountId: account.id, blankKeywordPutCount: filterCalls.length, failedStatus: emptyFilter.response.status(), settingsPreserved: true, previewStatus: preview.response.status() });

    // Notification rules use the fake adapter. Push targets are synthetic and never leave the preview service.
    await page.locator('[data-setting-tab="policies"]').click();
    await page.locator('[data-setting-panel="policies"]').waitFor({ state: 'visible' });
    await page.locator('[data-action="new-policy"]').click();
    const policyDialog = page.locator('dialog[data-dialog="policy"]');
    await policyDialog.locator('[name="name"]').fill('隔离验收Push策略');
    await policyDialog.locator('[name="desktop_enabled"]').uncheck();
    await policyDialog.locator('[name="push_enabled"]').check();
    await policyDialog.locator('[name="push_provider"]').selectOption('ntfy');
    await policyDialog.locator('[name="push_url"]').fill('ntfy://preview.invalid/integrations-check');
    const policySave = await expectResponse(policyDialog.locator('[type="submit"]'), 'POST', '/notification-policies');
    const policyId = policySave.data?.id;
    assertThat(Boolean(policyId), '通知策略保存响应缺少ID');
    assertThat(policySave.request.postDataJSON().push_url === 'ntfy://preview.invalid/integrations-check', '通知策略没有提交本地fake target');
    const policies = asArray(await apiGet('/notification-policies'));
    const savedPolicy = policies.find((item) => Number(item.id) === Number(policyId));
    assertThat(Boolean(savedPolicy?.push_enabled && savedPolicy?.has_push_url && !('push_url' in savedPolicy)), '通知策略没有安全保存Push target');
    const policyTest = await expectResponse(page.locator(`[data-policy-test="${policyId}"]`), 'POST', `/notification-policies/${policyId}/test`);
    assertThat(policyTest.data?.status === 'sent', '通知策略fake发送测试没有成功');
    note('notification-policy-fake-test', { id: policyId, push_enabled: savedPolicy.push_enabled, has_push_url: savedPolicy.has_push_url, testStatus: policyTest.data.status });

    // The primary notifications panel belongs to mail and must persist a fake retry result.
    await page.goto(`${BASE_URL}/workbench?view=mail&tab=notifications`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-mail-view]').waitFor({ timeout: 20000 });
    await page.locator('[data-notifications-panel] [data-notification-list]').waitFor();
    const notificationRows = asArray(await apiGet('/notifications'));
    const failedNotification = notificationRows.find((item) => item.status === 'failed');
    assertThat(Boolean(failedNotification), 'fake服务没有提供可重试失败通知');
    const retry = await expectResponse(page.locator(`[data-notification-retry="${failedNotification.id}"]`), 'POST', `/notifications/${failedNotification.id}/retry`);
    assertThat(retry.data?.status === 'sent', 'fake通知重试没有返回已发送状态');
    const notificationsAfterRetry = asArray(await apiGet('/notifications'));
    const retriedNotification = notificationsAfterRetry.find((item) => Number(item.id) === Number(failedNotification.id));
    assertThat(retriedNotification?.status === 'sent' && Number(retriedNotification.attempt_count) > Number(failedNotification.attempt_count), '通知重试结果未持久化');
    note('notification-history-fake-retry', { id: failedNotification.id, status: retriedNotification.status, attempt_count: retriedNotification.attempt_count });

    // Mail multi-entry, displayed suggestion version, manual facts, association, and missing-original behavior.
    await page.goto(`${BASE_URL}/workbench?view=mail`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-mail-view]').waitFor({ timeout: 20000 });
    await page.locator('[data-email-row]').first().waitFor();
    const mailPage = asArray(await apiGet('/email-messages?limit=500&offset=0'));
    const multiEmail = mailPage.find((item) => asArray(item.job_entries).length >= 2);
    const mailDetails = await Promise.all(mailPage.map(async (item) => ({ listItem: item, detail: await apiGet(`/email-messages/${item.id}`) })));
    const missingBodyRecord = mailDetails.find(({ detail }) => !detail.body_text);
    const missingBodyEmail = missingBodyRecord?.listItem;
    assertThat(Boolean(multiEmail && missingBodyEmail), 'fake邮件列表缺少多条目邮件或正文未保留邮件');
    await page.locator(`[data-open-email="${multiEmail.id}"]`).click();
    const emailDialog = page.locator('dialog[data-dialog="email"]');
    await emailDialog.waitFor({ state: 'visible' });
    await emailDialog.locator('[data-entry-card]').nth(1).waitFor();
    const entryCards = emailDialog.locator('[data-entry-card]');
    assertThat(await entryCards.count() >= 2, '邮件详情没有呈现所有岗位条目');

    const entries = asArray(multiEmail.job_entries);
    const suggestions = await Promise.all(entries.map(async (entry) => ({ entry, suggestions: asArray(await apiGet(`/email-job-entries/${entry.id}/progress-suggestions`)) })));
    const suggested = suggestions.flatMap(({ entry, suggestions: items }) => items.filter((item) => item.status === 'pending').map((item) => ({ entry, item })))[0];
    assertThat(Boolean(suggested), 'fake服务没有提供待确认进度建议');
    const acceptButton = emailDialog.locator(`[data-suggestion-accept="${suggested.item.id}"]`);
    await acceptButton.waitFor({ timeout: 15000 });
    const displayedSuggestionVersion = await acceptButton.getAttribute('data-suggestion-version');
    const displayedApplicationVersion = await acceptButton.getAttribute('data-application-version');
    assertThat(Boolean(displayedSuggestionVersion && displayedApplicationVersion), '建议按钮没有保存渲染时的版本快照');
    const accept = await expectResponse(acceptButton, 'POST', `/progress-suggestions/${suggested.item.id}/accept`);
    const acceptBody = accept.request.postDataJSON();
    assertThat(Number(acceptBody.expected_suggestion_version) === Number(displayedSuggestionVersion), '确认请求未使用屏幕展示的建议版本');
    assertThat(Number(acceptBody.expected_application_version) === Number(displayedApplicationVersion), '确认请求未使用屏幕展示的申请版本');
    const applicationsAfterAccept = asArray(await apiGet('/applications'));
    const expectedApplicationId = Number(suggested.item.application_id || suggested.entry.application_id);
    const acceptedApplication = applicationsAfterAccept.find((item) => Number(item.id) === expectedApplicationId);
    assertThat(Boolean(acceptedApplication), '确认建议后找不到目标申请记录');
    assertThat(Boolean(suggested.item.target_status) && acceptedApplication.status === suggested.item.target_status, '人工确认后申请状态未更新为建议目标状态');
    // The legacy /applications DTO intentionally omits the new optimistic
    // version field. Read the same record through the Workbench DTO so this
    // compatibility check does not rely on a field the old API never exposed.
    const acceptedWorkbenchJob = await apiGet(`/workbench/jobs/${suggested.entry.job_id}`);
    const acceptedWorkbenchApplication = acceptedWorkbenchJob?.application;
    assertThat(Number(acceptedWorkbenchApplication?.id) === expectedApplicationId, '工作台详情没有返回确认后的目标申请');
    assertThat(Number(acceptedWorkbenchApplication.version) > Number(displayedApplicationVersion), '人工确认后申请版本没有递增');
    note('progress-suggestion-accept', { suggestionId: suggested.item.id, applicationId: expectedApplicationId, target_status: acceptedApplication.status, expectedSuggestionVersion: Number(displayedSuggestionVersion), expectedApplicationVersion: Number(displayedApplicationVersion), responseStatus: accept.response.status() });

    const factsEntry = entries.find((entry) => Number(entry.id) !== Number(suggested.entry.id)) || entries[1];
    const entryCard = emailDialog.locator(`[data-entry-card="${factsEntry.id}"]`);
    await entryCard.locator(`[data-entry-facts="${factsEntry.id}"]`).click();
    const factsDialog = page.locator('dialog[data-dialog="manual-facts"]');
    await factsDialog.waitFor({ state: 'visible' });
    await factsDialog.locator('[name="company_name"]').fill(factsEntry.company_name || '隔离核验公司');
    await factsDialog.locator('[name="job_title"]').fill(factsEntry.job_title || '隔离核验岗位');
    await factsDialog.locator('[name="work_location"]').fill(factsEntry.work_location || '厦门');
    await factsDialog.locator('[name="event_type"]').selectOption('INTERVIEW');
    await factsDialog.locator('[name="event_round"]').fill('二面');
    await factsDialog.locator('[name="field_evidence"]').fill('合成邮件原文：邀请参加第二轮面试，请核对岗位和公司。');
    const factsSave = await expectResponse(factsDialog.locator('[type="submit"]'), 'POST', `/email-job-entries/${factsEntry.id}/manual-facts`);
    const factsBody = factsSave.request.postDataJSON();
    assertThat(factsBody.event_type === 'INTERVIEW' && factsBody.event_round === '二面' && Boolean(factsBody.field_evidence?.source_excerpt), '人工事实提交缺少事件类型、轮次或原文依据');
    let emailDetail = await apiGet(`/email-messages/${multiEmail.id}`);
    let savedEntry = asArray(emailDetail.job_entries).find((item) => Number(item.id) === Number(factsEntry.id));
    assertThat(savedEntry?.facts_source === 'manual' && savedEntry?.event_round === '二面', '人工事实没有持久化');
    await factsDialog.waitFor({ state: 'hidden' });

    const applicationsBeforeAssociation = asArray(await apiGet('/applications'));
    const statusSnapshot = new Map(applicationsBeforeAssociation.map((item) => [Number(item.id), item.status]));
    const jobs = asArray(await apiGet('/jobs'));
    assertThat(jobs.length >= 56, `fake岗位列表未覆盖全部56条本地岗位（实际${jobs.length}）`);
    const associationTarget = jobs.find((job) => Number(job.id) !== Number(factsEntry.job_id));
    assertThat(Boolean(associationTarget), '缺少可供关联的目标岗位');
    const jobSelect = entryCard.locator('[data-entry-job]');
    assertThat(await jobSelect.locator('option').count() >= jobs.length + 1, '人工关联选择器没有呈现完整岗位列表');
    await jobSelect.selectOption(String(associationTarget.id));
    const selectedJob = Number(await entryCard.locator('[data-entry-job]').inputValue());
    const associate = await expectResponse(entryCard.locator(`[data-entry-associate="${factsEntry.id}"]`), 'PATCH', `/email-job-entries/${factsEntry.id}/association`);
    const associationBody = associate.request.postDataJSON();
    assertThat(Number(associationBody.job_id) === selectedJob, `关联请求没有使用界面选择的岗位（界面=${selectedJob}，请求=${associationBody.job_id}）`);
    emailDetail = await apiGet(`/email-messages/${multiEmail.id}`);
    savedEntry = asArray(emailDetail.job_entries).find((item) => Number(item.id) === Number(factsEntry.id));
    assertThat(Number(savedEntry?.job_id) === selectedJob, '岗位人工关联没有持久化');
    const applicationsAfterAssociation = asArray(await apiGet('/applications'));
    for (const application of applicationsAfterAssociation) if (statusSnapshot.has(Number(application.id))) assertThat(application.status === statusSnapshot.get(Number(application.id)), '岗位关联意外改变了申请状态');
    note('mail-multi-entry-facts-association', { emailId: multiEmail.id, entryCount: entries.length, factsEntryId: factsEntry.id, jobId: savedEntry.job_id, applicationStatusesUnchanged: true });
    await emailDialog.locator('[data-close]').click();

    await page.locator(`[data-open-email="${missingBodyEmail.id}"]`).click();
    await page.locator('dialog[data-dialog="email"] .wb-original').waitFor({ state: 'visible' });
    const originalSection = page.locator('dialog[data-dialog="email"] .wb-original');
    await originalSection.locator('summary').click();
    const originalText = await originalSection.innerText();
    assertThat(originalText.includes('服务端没有保留原始正文'), '正文缺失时没有明确显示不可用提示');
    const missingPreview = missingBodyEmail.body_preview;
    if (missingPreview) assertThat(!originalText.includes(missingPreview), '仅摘要被错误显示成邮件原文');
    note('mail-missing-original-body', { emailId: missingBodyEmail.id, bodyRetained: false, previewShownAsOriginal: false });
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'mail-details.png'), fullPage: true });

    await page.locator('dialog[data-dialog="email"] [data-close]').click();
    const settingsEntry = page.locator('nav [data-workbench-view="settings"]').first();
    await settingsEntry.click();
    await page.locator('[data-settings-view]').waitFor({ timeout: 15000 });
    await page.locator('[data-setting-tab="policies"]').click();
    await page.screenshot({ path: path.join(OUTPUT_DIR, 'settings-integrations.png'), fullPage: true });

    assertThat(report.blockedExternalRequests.length === 0, '页面尝试访问了本地fake服务之外的网络地址');
    assertThat(report.pageErrors.length === 0, `浏览器发生页面异常：${report.pageErrors.join(' | ')}`);
    assertThat(report.consoleErrors.length === 0, `浏览器发生console错误：${report.consoleErrors.join(' | ')}`);
    note('browser-isolation-and-console', { blockedExternalRequests: 0, pageErrors: 0, consoleErrors: 0 });
    report.ok = true;
  } catch (error) {
    report.ok = false;
    report.error = redact(error?.message || String(error));
    if (page) {
      try { await page.screenshot({ path: path.join(OUTPUT_DIR, 'integrations-failure.png'), fullPage: true }); } catch { /* page may not have loaded */ }
    }
    throw error;
  } finally {
    report.finishedAt = new Date().toISOString();
    report.requests = redact(report.requests);
    report.consoleErrors = redact(report.consoleErrors);
    report.pageErrors = redact(report.pageErrors);
    report.blockedExternalRequests = redact(report.blockedExternalRequests);
    fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2), 'utf8');
    await browser.close();
    console.log(JSON.stringify({ ok: report.ok, report: REPORT_PATH, checks: report.checks.map((item) => item.name), requestCount: report.requests.length, blockedExternalRequests: report.blockedExternalRequests.length }));
  }
}

main().catch((error) => {
  console.error(`工作台集成浏览器验收失败：${redact(error?.message || String(error))}`);
  process.exitCode = 1;
});
