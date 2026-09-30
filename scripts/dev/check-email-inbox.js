/*
 * Playwright callback for the isolated email inbox preview.
 * Run through playwright-cli run-code --filename; the preview server is
 * synthetic and this callback never talks to FastAPI, SQLite, IMAP or LLMs.
 */
async (page, options = {}) => {
  const baseURL = options.baseURL || 'http://127.0.0.1:18767';
  const screenshotPath = options.screenshotPath || 'output/preview/email-inbox.png';
  const calls = [];
  const emailFixtures = [
    {
      id: 1, email_account_id: 7, sender: 'hr@example.com', subject: '面试邀请 · 数据分析实习',
      received_at: '2026-09-17T02:00:00Z', synced_at: '2026-09-17T02:01:00Z', body_preview: '请确认周五上午的面试时间。',
      pre_filtered: true, llm_processed: true, cleaning_status: 'completed', cleaning_result_id: 101,
      archive_status: 'archived', job_entries: [{ id: 301, cleaning_id: 101, company_id: 11, job_id: 21, application_id: null,
        recruitment_event_id: 401, company_name: '示例科技', job_title: '数据分析实习生', work_location: '厦门',
        links: ['https://jobs.example.test/21'], field_evidence: { company: '正文首行' }, event_type: 'INTERVIEW',
        event_time: '2026-09-19T02:00:00Z', deadline: '2026-09-18T12:00:00Z', action_required: true,
        actions: ['回复 HR'], summary: '邀请参加线上面试。', match_status: 'auto_bound', match_reason: '岗位链接一致', manually_associated: false,
        candidates: [{ job_id: 21, company_id: 11, application_id: null, company_name: '示例科技', job_title: '数据分析实习生', score: 98, reasons: ['岗位链接一致'] }] }],
      events: [{ id: 401, company_name: '示例科技', job_title: '数据分析实习生', event_type: 'INTERVIEW', event_time: '2026-09-19T02:00:00Z', ai_summary: '线上面试邀请。', association_status: 'confirmed' }],
    },
    {
      id: 2, email_account_id: 7, sender: 'campus@example.com', subject: '校招岗位更新', received_at: '2026-09-16T02:00:00Z', synced_at: '2026-09-16T02:01:00Z',
      body_preview: '新的数据产品岗位已发布。', pre_filtered: true, llm_processed: true, cleaning_status: 'completed', cleaning_result_id: 102, archive_status: 'archived',
      job_entries: [{ id: 302, cleaning_id: 102, company_name: '新公司', job_title: '数据产品实习生', match_status: 'auto_created', auto_created: true }], events: [],
    },
    {
      id: 3, email_account_id: 8, sender: 'recruit@example.com', subject: '岗位信息需要确认', received_at: '2026-09-15T02:00:00Z', synced_at: '2026-09-15T02:01:00Z',
      body_preview: '请补充岗位地点。', pre_filtered: true, llm_processed: true, cleaning_status: 'completed', cleaning_result_id: 103, archive_status: 'pending',
      job_entries: [{ id: 303, cleaning_id: 103, company_name: '待确认公司', job_title: '分析岗位', match_status: 'needs_review', match_reason: '候选岗位有冲突' }], events: [],
    },
    {
      id: 4, email_account_id: 8, sender: 'broken@example.com', subject: '邮件解析失败', received_at: '2026-09-14T02:00:00Z', synced_at: '2026-09-14T02:01:00Z',
      body_preview: '解析稍后重试。', pre_filtered: true, llm_processed: false, cleaning_status: 'failed', processing_error: '结构化结果暂不可用', cleaning_result_id: 104, archive_status: 'failed', job_entries: [], events: [],
    },
    {
      id: 5, email_account_id: 9, sender: 'queue@example.com', subject: '正在清洗的邮件', received_at: '2026-09-13T02:00:00Z', synced_at: '2026-09-13T02:01:00Z',
      body_preview: 'AI 正在提取岗位信息。', pre_filtered: true, llm_processed: false, cleaning_status: 'processing', cleaning_result_id: 105, archive_status: 'pending', job_entries: [], events: [],
    },
  ];
  const detailFixtures = new Map(emailFixtures.map((email) => [email.id, {
    ...email,
    body_text: '<img src=x onerror="window.__emailXss=1">\n请不要执行邮件 HTML。',
    cleaning: email.id === 1 ? {
      id: 101, status: 'completed', version: 1,
      body: '<img src=x onerror="window.__emailXss=1">\n清洗后的面试正文。', summary: '示例科技邀请参加数据分析实习生面试。',
      company_name: '示例科技', job_title: '数据分析实习生', work_location: '厦门', links: ['https://jobs.example.test/21'],
      field_evidence: { company_name: '正文首行', job_title: '岗位标题' }, event_time: '2026-09-19T02:00:00Z',
      deadline: '2026-09-18T12:00:00Z', action_required: true, actions: ['回复 HR'], error: null,
    } : { id: email.cleaning_result_id, status: email.cleaning_status, version: 1, body: email.body_preview, summary: email.body_preview },
  }]));

  // A failed callback can leave a route installed in the named browser
  // session.  Remove it before registering this run so a stale Node-side
  // handler cannot intercept the next run.
  await page.unroute('**/api/**').catch(() => {});
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    calls.push({ method: request.method(), path, body: request.postData() || '' });
    if (path === '/api/settings') return route.fulfill({ json: [] });
    if (path === '/api/email-messages' && request.method() === 'GET') return route.fulfill({ json: emailFixtures });
    if (path.startsWith('/api/email-messages/') && request.method() === 'GET') {
      const id = Number(path.split('/').pop());
      return detailFixtures.has(id) ? route.fulfill({ json: detailFixtures.get(id) }) : route.fulfill({ status: 404, json: { detail: 'not found' } });
    }
    if (path === '/api/recruitment-events') return route.fulfill({ json: [] });
    if (path === '/api/email-filter' && request.method() === 'GET') return route.fulfill({ json: { keywords: ['招聘'], regex: '(面试|笔试)' } });
    if (path === '/api/email-filter/preview' && request.method() === 'POST') return route.fulfill({ json: { matched: true } });
    if (path === '/api/email-accounts') return route.fulfill({ json: [] });
    if (path === '/api/notification-policies') return route.fulfill({ json: [] });
    if (path === '/api/llm/configs') return route.fulfill({ json: [] });
    if (path === '/api/llm/routing') return route.fulfill({ json: { auto_select_enabled: false, default_config_id: null, email_config_id: null, max_parallel_calls: 1 } });
    if (path.includes('/api/email-messages/') || path.includes('/api/email-job-entries/') || path.includes('/api/email-cleaning/')) return route.fulfill({ json: {} });
    return route.fulfill({ json: [] });
  });

  await page.goto(`${baseURL}/events`);
  await page.locator('#emailList .email-row').first().waitFor();
  const rowCount = await page.locator('#emailList .email-row').count();
  if (rowCount !== 5) throw new Error(`expected 5 email rows, received ${rowCount}`);
  const labels = await page.locator('#emailList .email-status-tag').allTextContents();
  for (const expected of ['已关联', '已新建', '待处理', '失败', '处理中']) if (!labels.includes(expected)) throw new Error(`missing inbox status: ${expected}`);

  await page.locator('#emailList .email-row').first().locator('[data-open-email]').first().click();
  await page.locator('#emailDialog .cleaned-body').waitFor();
  if ((await page.locator('#emailDialog .job-entry-card').count()) !== 1) throw new Error('job entry was not rendered');
  if ((await page.locator('#emailDialog .email-event').count()) !== 1) throw new Error('event was not rendered');
  const cleanedText = await page.locator('#emailDialog .cleaned-body').textContent();
  if (!cleanedText.includes('<img')) throw new Error('cleaned body was not shown as text');
  if (await page.evaluate(() => window.__emailXss)) throw new Error('email HTML executed');

  await page.locator('#emailDialog .email-original summary').click();
  if (!(await page.locator('#emailDialog .email-body').textContent()).includes('<img')) throw new Error('original body was not shown as text');
  const candidateSelect = page.locator('#emailDialog [data-entry-candidate]');
  await candidateSelect.selectOption({ index: 1 });
  if (!(await candidateSelect.inputValue())) throw new Error('candidate selector did not retain a selected job');
  await page.locator('#emailDialog [data-entry-associate]').click();
  await page.waitForTimeout(180);
  await page.locator('#emailDialog [data-entry-rematch]').click();
  await page.waitForTimeout(180);
  await page.locator('#reprocessEmail').click();
  await page.waitForTimeout(180);
  await page.locator('#retryArchive').click();
  await page.waitForTimeout(260);
  const requestPaths = calls.map((call) => `${call.method} ${call.path}`);
  for (const expected of [
    'PATCH /api/email-job-entries/301/association', 'POST /api/email-job-entries/301/rematch',
    'POST /api/email-cleaning/101/reprocess', 'POST /api/email-cleaning/101/archive',
  ]) if (!requestPaths.includes(expected)) throw new Error(`missing interaction request: ${expected}`);
  const associationCall = calls.find((call) => call.path === '/api/email-job-entries/301/association');
  if (!JSON.parse(associationCall.body).job_id) throw new Error('association payload did not include job_id');
  await page.locator('#emailDialog [data-close]').first().click();

  await page.goto(`${baseURL}/settings`);
  await page.locator('details.setting-row').nth(2).locator('summary').first().click();
  await page.locator('.regex-preview > summary').click();
  await page.locator('#emailRegexPreviewText').fill('发件人：hr@example.com\n主题：面试邀请');
  await page.locator('#previewEmailRegex').click();
  await page.locator('#emailRegexPreviewResult').waitFor();
  if (!(await page.locator('#emailRegexPreviewResult').textContent()).includes('命中')) throw new Error('regex preview did not report a match');
  const previewCall = calls.find((call) => call.path === '/api/email-filter/preview');
  const previewBody = JSON.parse(previewCall.body);
  if (previewBody.regex !== '(面试|笔试)' || !previewBody.body_text.includes('面试')) throw new Error('regex preview payload was not posted');

  const checks = [];
  for (const width of [1189, 700, 390]) {
    await page.goto(`${baseURL}/events`);
    await page.setViewportSize({ width, height: 900 });
    await page.locator('#emailList .email-row').first().waitFor();
    const result = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth, rows: document.querySelectorAll('#emailList .email-row').length }));
    if (result.overflow) throw new Error(`horizontal overflow at ${width}px`);
    checks.push({ width, ...result });
  }
  await page.setViewportSize({ width: 1189, height: 900 });
  await page.goto(`${baseURL}/events`);
  await page.locator('#emailList .email-row').first().waitFor();
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return { rowCount, labels, checks, requestPaths };
}
