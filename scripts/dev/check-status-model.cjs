async (page) => {
  const base = 'http://127.0.0.1:18769';
  if (!page.url().startsWith(base + '/')) throw new Error('Only the synthetic preview is allowed');
  const failures = [];
  page.on('pageerror', (error) => failures.push(error.message));
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const ready = () => page.locator('[data-job-results][aria-busy="false"]').waitFor();
  const unique = Date.now();
  const created = await page.request.post(`${base}/api/workbench/jobs`, { data: {
    company: { name: `界面验收公司-${unique}` }, job: { title: '软件运维工程师 · 界面验收', location: '杭州', employment_type: '校招', source_url: 'https://example.test/apply', salary_text: '15–20K' },
    idempotency_key: `ui-${unique}`,
  }});
  assert(created.ok(), 'fixture job creation');
  const item = (await created.json()).item;
  await page.goto(`${base}/workbench?view=library`);
  await ready();
  const jobId = item.id;
  const quick = () => page.locator(`[data-quick-status="${jobId}"]`);
  assert((await quick().locator('option').allTextContents()).join(',') === '待投递,已投递,待面试,1面,2面,3面,HR面,拒绝', 'eight canonical statuses');
  await quick().selectOption('1面');
  await page.getByRole('button', { name: '撤销', exact: true }).last().waitFor();
  assert(await quick().inputValue() === '1面', 'inline status persisted');
  await page.getByRole('button', { name: '撤销', exact: true }).last().click();
  await page.waitForFunction((id) => document.querySelector(`[data-quick-status="${id}"]`)?.value === '待投递', jobId);
  await page.locator(`[data-job-id="${jobId}"] [data-open-workbench-job]`).last().click();
  await page.locator('[data-selected-dialog][open]').waitFor();
  assert((await page.request.get(`${base}/api/workbench/jobs/${jobId}`).then((r) => r.json())).application.status === '已投递', 'detail click advances progress');
  await page.keyboard.press('Escape');
  await ready();
  await page.locator('[name="q"]').fill('软件运维');
  await page.waitForURL(/q=/);
  await ready();
  assert(await page.locator('[name="q"]').evaluate((el) => el === document.activeElement), 'search keeps keyboard focus');
  await page.locator('[data-direction="运维"]').click();
  await ready();
  assert(page.url().includes('direction='), 'direction encoded in URL');
  await page.locator('[data-clear-filters]').click();
  await ready();
  for (const width of [1480, 1242, 920, 390]) {
    await page.setViewportSize({ width, height: 764 });
    await page.evaluate(() => window.scrollTo(0, 0));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `library overflow ${width}`);
    await page.screenshot({ path: `output/playwright/status-model/library-${width}.png` });
  }
  await page.setViewportSize({ width: 1480, height: 764 });
  await page.goto(`${base}/workbench?view=settings`);
  await page.locator('[data-model-edit="1"]').waitFor();
  await page.locator('[data-model-edit="1"]').click();
  const dialog = page.locator('[data-dialog="model"]');
  assert(await dialog.evaluate((el) => el.matches(':modal')), 'native modal used');
  for (const width of [1480, 1242, 920, 390]) {
    await page.setViewportSize({ width, height: 764 });
    assert(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth), `dialog overflow ${width}`);
    await page.screenshot({ path: `output/playwright/status-model/model-dialog-${width}.png` });
  }
  await page.setViewportSize({ width: 1242, height: 764 });
  await dialog.locator('[name="display_name"]').fill('合成模型 · 编辑验证');
  page.once('dialog', (prompt) => prompt.dismiss());
  await page.keyboard.press('Escape');
  assert(await dialog.evaluate((el) => el.open), 'unsaved escape can be cancelled');
  await dialog.locator('[name="health_mode"]').selectOption('custom');
  await dialog.locator('[name="health_interval"]').fill('5');
  await dialog.locator('[type="submit"]').click();
  await dialog.waitFor({ state: 'hidden' });
  await page.locator('[data-model-edit="1"]').click();
  assert(await dialog.locator('[name="health_interval"]').inputValue() === '5', 'model interval persisted');
  await page.keyboard.press('Escape');
  assert(await page.locator('[data-model-edit="1"]').evaluate((el) => el === document.activeElement), 'focus returns to editor trigger');
  await page.locator('[data-model-test="1"]').click();
  await page.locator('[data-model-card="1"] [data-health="passed"]').waitFor();
  const settings = page.locator('[data-health-form]');
  await settings.locator('[name="enabled"]').check();
  await settings.locator('[name="interval_minutes"]').fill('15');
  await settings.locator('[type="submit"]').click();
  await settings.locator('[data-feedback]').filter({ hasText: '已保存' }).waitFor();
  const saved = await page.request.get(`${base}/api/llm/health-settings`).then((r) => r.json());
  assert(saved.enabled && saved.interval_minutes === 15, 'global interval saved');
  for (const width of [1480, 1242, 920, 390]) {
    await page.setViewportSize({ width, height: 764 });
    await page.locator('[data-model-card="1"]').scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `settings overflow ${width}`);
    await page.screenshot({ path: `output/playwright/status-model/settings-${width}.png` });
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('[data-model-edit="1"]').click();
  assert(await dialog.evaluate((el) => getComputedStyle(el).animationName) === 'none', 'reduced motion respected');
  await page.keyboard.press('Escape');
  assert(!failures.length, failures.join('\n'));
  await page.evaluate(() => { document.body.dataset.statusModelAcceptance = 'passed'; });
  return 'Passed: status, undo, auto-progress, live filtering/focus, modal, model test/intervals, four widths and reduced motion.';
}
