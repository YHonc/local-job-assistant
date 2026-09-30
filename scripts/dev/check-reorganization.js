async (page) => {
  const baseURL = 'http://127.0.0.1:18767';
  const errors = [];
  const writes = [];
  const unexpected = [];
  const companies = [{ id: 1, name: '隔离示例公司', aliases: [], notes: null }];
  const jobs = [174, 175].map((id) => ({ id, company_id: 1, title: id === 174 ? '数据分析示例' : '产品示例', location: '厦门', job_category: '数据分析', employment_type: '实习', created_at: '2026-09-01T00:00:00Z', published_at: '2026-09-01', description: '隔离测试岗位', source_url: 'https://example.com/job' }));
  const applications = jobs.map((job) => ({ id: job.id, job_id: job.id, status: '收藏', notes: null }));
  let preferences = { theme: 'light', density: 'comfortable', poetry_preset: 'random' };
  const model = { id: 1, display_name: '模拟模型', provider: 'openai', model: 'fixture', enabled: true, call_count: 2, success_count: 2, failure_count: 0, average_duration_ms: 100, total_prompt_tokens: 10, total_completion_tokens: 5 };
  const routing = { auto_select_enabled: false, default_config_id: 1, email_config_id: null, max_parallel_calls: 1 };
  const totals = { call_count: 2, success_count: 2, failure_count: 0, average_duration_ms: 100, total_prompt_tokens: 10, total_completion_tokens: 5, total_tokens: 15 };
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  page.on('pageerror', error => errors.push(error.message));
  await page.unroute('**/api/**');
  await page.unroute('**/static/**/*.js*');
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = request.url();
    const path = url.split('?')[0].replace(/^https?:\/\/[^/]+/, '');
    const method = request.method();
    const body = method === 'GET' ? null : request.postDataJSON();
    let result;
    if (method !== 'GET') writes.push({ path, method });
    if (path === '/api/settings') result = Object.entries(preferences).map(([key, value]) => ({ key, value }));
    else if (path === '/api/settings/preferences') { preferences = body; result = []; }
    else if (path === '/api/companies') result = companies;
    else if (path === '/api/jobs' && method === 'POST') { result = { ...body, id: 176, created_at: '2026-09-17T00:00:00Z' }; jobs.push(result); }
    else if (path === '/api/jobs') result = jobs;
    else if (/^\/api\/jobs\/\d+$/.test(path)) {
      result = jobs.find(job => job.id === Number(path.split('/').pop()));
      if (method === 'PATCH') Object.assign(result, body);
    } else if (path === '/api/applications/bulk-status') {
      result = applications.filter(item => body.application_ids.includes(item.id));
      result.forEach(item => { item.status = body.status; });
    } else if (path === '/api/applications' && method === 'POST') { result = { ...body, id: 176 }; applications.push(result); }
    else if (path === '/api/applications') result = applications;
    else if (/^\/api\/applications\/\d+$/.test(path)) {
      result = applications.find(item => item.id === Number(path.split('/').pop()));
      Object.assign(result, body);
    } else if (path === '/api/llm/configs') result = [model];
    else if (path === '/api/llm/configs/1') { result = model; if (method === 'PUT') Object.assign(model, body); }
    else if (path === '/api/llm/routing') { result = routing; if (method === 'PUT') Object.assign(routing, body); }
    else if (path === '/api/llm/configs/1/usage') result = { start: url.match(/[?&]start=([^&]+)/)?.[1], end: url.match(/[?&]end=([^&]+)/)?.[1], granularity: 'day', recorded_from: '2026-09-17T00:00:00Z', legacy_call_count: 0, lifetime: totals, summary: totals, points: [{ timestamp: '2026-09-17T00:00:00Z', available: true, ...totals }] };
    else if (path === '/api/email-filter') result = { keywords: ['面试'], regex: null };
    else if (['/api/email-accounts', '/api/notification-policies', '/api/notifications', '/api/recruitment-events'].includes(path)) result = [];
    else { unexpected.push(`${method} ${path}`); return route.fulfill({ status: 500, json: { detail: 'Unmocked fixture route' } }); }
    return route.fulfill({ json: result });
  });
  await page.goto(baseURL);
  await page.locator('.job-card').last().waitFor();
  assert(await page.locator('.job-card').count() === 2, 'Tracker did not render fixtures');
  await page.locator('#search').fill('数据分析示例');
  assert(await page.locator('.job-card').count() === 1, 'Tracker filtering failed');
  await page.locator('.job-detail-link').click();
  await page.locator('#jobDetail').waitFor();
  await page.goBack();
  await page.locator('.job-card').waitFor();
  assert(await page.locator('#search').inputValue() === '数据分析示例', 'Tracker history was not restored');
  await page.locator('#search').fill('');
  await page.locator('[data-quick-status="174"]').selectOption('已投递');
  await page.waitForFunction(() => document.querySelector('[data-quick-status="174"]')?.value === '已投递' && !document.querySelector('[data-quick-status="174"]').disabled);
  assert(applications[0].status === '已投递', 'Inline status was not saved');
  await page.locator('[data-select-job="174"]').check();
  await page.locator('#bulkStatus').selectOption('笔试');
  await page.locator('#applyBulkStatus').click();
  await page.waitForFunction(() => document.querySelector('#feedback').textContent.includes('更新 1 项'));
  assert(applications[0].status === '笔试', 'Bulk update failed');
  await page.locator('#newJob').click();
  await page.locator('#title').fill('新增隔离示例');
  await page.locator('#jobForm button[type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#jobDialog').open);
  assert(jobs.length === 3 && applications.length === 3, 'Job/Application save failed');
  await page.goto(baseURL + '/settings');
  await page.waitForFunction(() => document.querySelector('#llmConfigBadge').textContent.includes('1 个配置'));
  await page.locator('summary').filter({ hasText: '界面偏好' }).click();
  await page.locator('#theme').selectOption('dark');
  await page.locator('#settingsForm button[type="submit"]').click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  await page.locator('summary').filter({ hasText: '模型池与路由' }).click();
  await page.locator('[data-llm-usage="1"]').click();
  await page.waitForFunction(() => document.querySelector('#usageChart').getAttribute('aria-busy') === 'false');
  assert(await page.locator('#usageFeedback.error').count() === 0, 'Usage dialog failed');
  await page.locator('#usageDialog [data-close]').first().click();
  await page.locator('[data-llm-edit="1"]').click();
  await page.locator('#llmDisplayName').fill('模拟模型已编辑');
  await page.locator('#llmConfigForm button[type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#llmConfigDialog').open);
  assert(model.display_name === '模拟模型已编辑', 'Model edit failed');
  await page.locator('#llmMaxParallel').fill('2');
  await page.locator('#saveLLMRouting').click();
  await page.waitForFunction(() => document.querySelector('#llmConfigFeedback').textContent === '路由已保存');
  assert(routing.max_parallel_calls === 2, 'Routing save failed');
  for (const path of ['/events', '/notifications']) {
    await page.goto(baseURL + path);
    await page.locator('.empty').waitFor();
  }
  assert(errors.length === 0, `Page errors: ${errors.join('; ')}`);
  assert(unexpected.length === 0, `Unexpected APIs: ${unexpected.join('; ')}`);
  assert(writes.filter(item => item.path === '/api/settings/preferences').length === 1, 'Duplicate preference handlers');
  assert(writes.filter(item => item.path === '/api/jobs').length === 1, 'Duplicate job handlers');
  return { passed: true, pageErrors: errors.length, writes: writes.length, checks: ['tracker filtering', 'history restore', 'inline status', 'bulk status', 'job save', 'preferences', 'usage dialog', 'model edit', 'routing', 'events', 'notifications'] };
}
