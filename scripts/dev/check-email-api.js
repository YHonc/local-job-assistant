async (page) => {
  const base = 'http://127.0.0.1:18768';
  const calls = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().includes('/api/')) calls.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
  });
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const read = async path => { const response = await page.request.get(base + path); assert(response.ok(), path); return response.json(); };
  await page.goto(base + '/events');
  await page.locator('.email-row').first().waitFor();
  const labels = await page.locator('.email-row .email-status-tag').allTextContents();
  for (const label of ['处理中', '未命中筛选', '失败', '待处理', '已新建', '已关联']) assert(labels.includes(label), 'missing ' + label);
  const before = await read('/api/applications');
  const open = async id => {
    await page.locator(`[data-email-id="${id}"] [data-open-email]`).first().click();
    await page.locator('#emailContent .cleaning-result').waitFor();
  };
  const action = async (selector, method, path) => {
    const responsePromise = page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname === path);
    await page.locator(selector).click();
    const response = await responsePromise;
    assert(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
    await page.waitForFunction(() => !document.querySelector('#emailDialog .is-loading'));
  };
  await open(3);
  await page.locator('[data-entry-candidate]').selectOption({ label: '示例科技 · 数据分析实习生' });
  await action('[data-entry-associate]', 'PATCH', '/api/email-job-entries/3/association');
  await action('#rematchEmail', 'POST', '/api/email-job-entries/3/rematch');
  await action('#reprocessEmail', 'POST', '/api/email-cleaning/3/reprocess');
  await action('#retryArchive', 'POST', '/api/email-cleaning/3/archive');
  const manual = await read('/api/email-messages/3');
  assert(manual.job_entries[0].manually_associated && manual.job_entries[0].job_id === 1, 'manual association lost');
  await page.locator('#emailDialog [data-close]').first().click();
  await open(4);
  await action('#retryCleaning', 'POST', '/api/email-cleaning/4/retry');
  assert((await read('/api/email-messages/4')).cleaning.status === 'completed', 'retry failed');
  await page.locator('#emailDialog [data-close]').first().click();
  await open(6);
  await action('#retryCleaning', 'POST', '/api/email-messages/6/process');
  assert((await read('/api/email-messages/6')).cleaning.status === 'completed', 'legacy processing failed');
  await page.locator('#emailDialog [data-close]').first().click();
  const after = await read('/api/applications');
  for (const application of before) assert(after.find(item => item.id === application.id)?.status === application.status, 'application status changed');
  await page.goto(base + '/settings');
  const preview = page.locator('#emailRegexPreviewText');
  await preview.locator('xpath=ancestor::details').locator('summary').first().click();
  await page.locator('.regex-preview > summary').click();
  await preview.fill('面试邀请');
  await action('#previewEmailRegex', 'POST', '/api/email-filter/preview');
  assert((await page.locator('#emailRegexPreviewResult').textContent()).includes('命中'), 'regex preview failed');
  const widths = [];
  await page.goto(base + '/events');
  await page.locator('.email-row').first().waitFor();
  for (const width of [1189, 700, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'overflow ' + width);
    widths.push(width);
  }
  await page.setViewportSize({ width: 1189, height: 900 });
  await page.screenshot({ path: '.tmp/email-api-acceptance.png', fullPage: true });
  assert(errors.length === 0, errors.join('\n'));
  assert(calls.every(call => call.status < 400), JSON.stringify(calls.filter(call => call.status >= 400)));
  return { labels, widths, applicationsPreserved: before.length, calls, errors };
}
