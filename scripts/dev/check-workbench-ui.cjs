const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const BASE_URL = 'http://127.0.0.1:18769';
const ORIGIN = new URL(BASE_URL).origin;
const OUTPUT_DIR = path.resolve(__dirname, '../../output/playwright/workbench');
const REPORT_PATH = path.join(OUTPUT_DIR, 'ui-acceptance.json');

function assertThat(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const playwrightRuntime = process.argv[2];
  if (!playwrightRuntime) throw new Error('Usage: node scripts/dev/check-workbench-ui.cjs <playwright-core-directory>');
  const { chromium } = require(path.resolve(playwrightRuntime));
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const report = { baseURL: BASE_URL, isolation: 'synthetic preview only', checks: [], pageErrors: [], consoleErrors: [], blockedExternalRequests: [] };
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();

  try {
    page.on('pageerror', (error) => report.pageErrors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
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
      const response = await page.request.get(`${BASE_URL}/api${resource}`);
      const data = await response.json();
      assertThat(response.ok(), `GET ${resource} failed with ${response.status()}`);
      return data;
    };
    const waitForPath = async (view) => page.waitForURL((url) => url.searchParams.get('view') === view, { timeout: 15000 });
    const check = (name, detail = {}) => report.checks.push({ name, ...detail });

    await page.goto(`${BASE_URL}/workbench?view=pending`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-drafts] [data-draft-card]').first().waitFor({ timeout: 20000 });
    assertThat((await page.locator('[data-draft-count]').textContent()).includes('22'), 'pending draft total is not 22');
    assertThat(await page.locator('[data-draft-card]').count() === 20, 'pending first page does not contain 20 drafts');
    assertThat(!(await page.locator('[data-draft-next]').isDisabled()), 'pending draft next page is disabled');
    await Promise.all([waitForPath('pending'), page.locator('[data-draft-next]').click()]);
    await page.locator('[data-draft-card]').first().waitFor();
    assertThat(new URL(page.url()).searchParams.get('draft_offset') === '20', 'pending next page did not update URL');
    assertThat(await page.locator('[data-draft-card]').count() === 2, 'pending second page does not contain 2 drafts');
    check('pending-pagination', { total: 22, firstPage: 20, secondPage: 2 });

    await Promise.all([waitForPath('pending'), page.locator('[data-draft-prev]').click()]);
    await page.locator('[data-draft-card="2"]').waitFor();
    const draftCard = page.locator('[data-draft-card="2"]');
    const titleSuggestion = draftCard.locator('[data-suggestion-field="title"]');
    await titleSuggestion.check();
    const acceptResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/import-drafts/2/accept-suggestions' && response.request().method() === 'POST');
    await draftCard.locator('[data-accept-suggestions]').click();
    const acceptedResponse = await acceptResponse;
    assertThat(acceptedResponse.ok(), `draft suggestion acceptance failed with ${acceptedResponse.status()}`);
    await draftCard.locator('[data-draft-field="title"]').waitFor();
    assertThat(await draftCard.locator('[data-draft-field="title"]').inputValue() === '运营分析实习生', 'accepted draft suggestion did not enter the form');
    const acceptedDraft = await apiGet('/import-drafts/2');
    assertThat(acceptedDraft.status === 'extracted' && acceptedDraft.version === 3 && acceptedDraft.user_fields.title === '运营分析实习生', 'draft suggestion acceptance persisted an unexpected state');
    check('draft-suggestion-acceptance', { draftId: 2, acceptedFields: ['title'], status: acceptedDraft.status, version: acceptedDraft.version });

    const createDraft = page.locator('[data-create-draft]');
    await createDraft.locator('[name="captured_text"]').fill('未保存的页面文本，仅用于离开保护验收。');
    let dismissed = false;
    page.once('dialog', async (dialog) => { dismissed = dialog.type() === 'confirm'; await dialog.dismiss(); });
    await page.locator('nav [data-workbench-view="library"]').first().click();
    await page.waitForTimeout(300);
    assertThat(dismissed, 'navigation guard did not show a confirmation dialog');
    assertThat(new URL(page.url()).searchParams.get('view') === 'pending', 'cancelled navigation left the pending view');
    assertThat((await createDraft.locator('[name="captured_text"]').inputValue()).includes('未保存'), 'cancelled navigation lost the unsaved draft input');
    let acceptedLeave = false;
    page.once('dialog', async (dialog) => { acceptedLeave = dialog.type() === 'confirm'; await dialog.accept(); });
    await page.locator('nav [data-workbench-view="library"]').first().click();
    await page.locator('[data-job-results] [data-job-id]').first().waitFor({ timeout: 20000 });
    assertThat(acceptedLeave, 'accepted navigation did not use the confirmation dialog');
    check('unsaved-draft-navigation-guard', { cancelledNavigationKeptInput: true, acceptedNavigationCompleted: true });

    await page.goto(`${BASE_URL}/workbench?view=library`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-job-results] [data-job-id]').first().waitFor({ timeout: 20000 });
    const locationFacet = page.locator('[data-facet="locations"]');
    const typeFacet = page.locator('[data-facet="employment_types"]');
    await locationFacet.locator('summary').click();
    const selectedLocation = await locationFacet.locator('input[type="checkbox"]').first().inputValue();
    await locationFacet.locator('input[type="checkbox"]').first().check();
    await typeFacet.locator('summary').click();
    const selectedType = await typeFacet.locator('input[type="checkbox"]').first().inputValue();
    await typeFacet.locator('input[type="checkbox"]').first().check();
    assertThat(!(await locationFacet.locator('details').evaluate((details) => details.open)), 'opening the type filter did not close the location filter');
    await page.locator('[data-filter-form]').evaluate((form) => form.requestSubmit());
    await page.waitForURL((url) => url.searchParams.getAll('locations').includes(selectedLocation) && url.searchParams.getAll('employment_types').includes(selectedType));
    await page.locator('[data-job-results] [data-job-id]').first().waitFor();
    assertThat((await locationFacet.locator('[data-facet-summary]').textContent()).includes(selectedLocation), 'location selection was not restored after filtering');
    assertThat((await typeFacet.locator('[data-facet-summary]').textContent()).includes(selectedType), 'employment type selection was not restored after filtering');
    check('library-facet-filters', { location: selectedLocation, employmentType: selectedType, urlRoundTrip: true });
    await page.locator('[data-clear-filters]').click();
    await page.waitForURL((url) => !url.searchParams.has('locations') && !url.searchParams.has('employment_types'));
    await page.locator('[data-job-results] [data-job-id]').first().waitFor();
    const firstJob = page.locator('[data-job-results] [data-job-id]').first();
    const firstJobId = await firstJob.getAttribute('data-job-id');
    await firstJob.locator('[data-select-job]').check();
    await firstJob.locator('[data-open-workbench-job]').click();
    await page.locator(`[data-selected-detail-job="${firstJobId}"]`).waitFor();
    await page.locator('[data-clear-job]').click();
    await page.waitForFunction(() => !document.querySelector('[data-selected-area]')?.innerHTML);
    assertThat(await page.locator(`[data-job-id="${firstJobId}"] [data-select-job]`).isChecked(), 'returning from job detail lost the selected job');
    check('library-detail-return-context', { jobId: Number(firstJobId), selectionRestored: true });

    await page.locator('[data-company-manager]').click();
    const companyDialog = page.locator('dialog.wb-company-dialog');
    await companyDialog.waitFor({ state: 'visible' });
    const companyRow = companyDialog.locator('[data-company-row]').first();
    await companyRow.waitFor();
    const companyId = await companyRow.getAttribute('data-company-row');
    await companyRow.locator('[data-company-edit]').click();
    const companyNotes = companyDialog.locator('[data-company-form] [name="notes"]');
    await companyNotes.fill('UI acceptance company note');
    const companySave = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/companies/${companyId}` && response.request().method() === 'PATCH');
    await companyDialog.locator('[data-company-save]').click();
    const savedCompanyResponse = await companySave;
    assertThat(savedCompanyResponse.ok(), `company save failed with ${savedCompanyResponse.status()}`);
    const savedCompany = await apiGet(`/companies/${companyId}`);
    assertThat(savedCompany.notes === 'UI acceptance company note', 'company notes did not persist independently');
    await companyDialog.locator('[data-company-close]').click();
    check('company-editor-save', { companyId: Number(companyId), notesPersisted: true });

    await page.locator(`[data-job-id="${firstJobId}"] [data-edit-job]`).click();
    const jobDialog = page.locator('dialog.wb-job-dialog');
    await jobDialog.waitFor({ state: 'visible' });
    await jobDialog.locator('[name="notes"]').fill('UI acceptance job note');
    await jobDialog.locator('[name="application_notes"]').fill('UI acceptance application note');
    const jobSave = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/workbench/jobs/${firstJobId}` && response.request().method() === 'PATCH');
    await jobDialog.locator('[data-job-save]').click();
    const savedJobResponse = await jobSave;
    assertThat(savedJobResponse.ok(), `job editor save failed with ${savedJobResponse.status()}`);
    const jobSaveBody = savedJobResponse.request().postDataJSON();
    assertThat(jobSaveBody.job?.notes === 'UI acceptance job note', 'job editor did not send job notes separately');
    assertThat(jobSaveBody.application?.notes === 'UI acceptance application note', 'job editor did not send application notes separately');
    const savedJob = await apiGet(`/workbench/jobs/${firstJobId}`);
    assertThat(savedJob.notes === 'UI acceptance job note' && savedJob.application.notes === 'UI acceptance application note', 'job and application notes did not persist separately');
    check('job-editor-cross-entity-save', { jobId: Number(firstJobId), jobNotesPersisted: true, applicationNotesPersisted: true });

    await page.goto(`${BASE_URL}/workbench?view=follow`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-follow-results] [data-follow-job]').first().waitFor({ timeout: 20000 });
    assertThat(await page.locator('.wb-follow-item').count() === 10, 'follow default page size is not 10');
    await page.locator('[data-follow-page-size]').selectOption('5');
    await page.waitForURL((url) => url.searchParams.get('limit') === '5');
    await page.waitForFunction(() => document.querySelectorAll('.wb-follow-item').length === 5);
    assertThat((await page.locator('[data-follow-page]').textContent()).includes('第 1 /'), 'follow page-size change did not reset to page 1');
    await page.locator('[data-follow-jump] input[name="page"]').fill('3');
    await page.locator('[data-follow-jump] button[type="submit"]').click();
    await page.waitForURL((url) => url.searchParams.get('offset') === '10');
    await page.waitForFunction(() => document.querySelector('[data-follow-page]')?.textContent?.includes('第 3 /'));
    assertThat(await page.locator('.wb-follow-item').count() === 5, 'follow page jump did not show five rows');
    await page.locator('[data-follow-page-size]').selectOption('20');
    await page.waitForURL((url) => url.searchParams.get('limit') === '20' && !url.searchParams.has('offset'));
    await page.waitForFunction(() => document.querySelectorAll('.wb-follow-item').length === 20);
    await page.locator('[data-follow-page-size]').selectOption('30');
    await page.waitForURL((url) => url.searchParams.get('limit') === '30');
    await page.waitForFunction(() => document.querySelectorAll('.wb-follow-item').length === 30);
    check('follow-page-size-and-jump', { choices: [5, 10, 20, 30], jumpOffset: 10, sizeChangeResetsPage: true });
    await page.goto(`${BASE_URL}/workbench?view=follow`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-follow-results] [data-follow-job]').first().waitFor({ timeout: 20000 });
    const followCard = page.locator('[data-follow-results] [data-follow-job]').first();
    const followJobId = await followCard.getAttribute('data-follow-job');
    const statusSelect = followCard.locator('[data-status-form] select[name="status"]');
    const currentStatus = await statusSelect.inputValue();
    const targetStatus = currentStatus === '一面' ? '二面' : '一面';
    const followResponse = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/workbench/jobs/${followJobId}` && response.request().method() === 'PATCH');
    await statusSelect.selectOption(targetStatus);
    await followCard.locator('[data-status-form] button[type="submit"]').click();
    const savedFollow = await followResponse;
    assertThat(savedFollow.ok(), `follow status save failed with ${savedFollow.status()}`);
    const refreshedFollow = await apiGet(`/workbench/jobs/${followJobId}`);
    assertThat(refreshedFollow.application.status === targetStatus, 'follow status did not persist through the Workbench API');
    check('follow-status-save', { jobId: Number(followJobId), from: currentStatus, to: targetStatus });

    await page.goto(`${BASE_URL}/workbench?view=mail`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-email-row]').first().waitFor({ timeout: 20000 });
    await page.locator('[data-email-row] [data-open-email]').first().click();
    await page.locator('[data-dialog="email"] [data-email-content] .wb-detail-section').first().waitFor();
    const desktopMail = await page.evaluate(() => {
      const list = document.querySelector('.wb-mail-list-column').getBoundingClientRect();
      const detail = document.querySelector('[data-dialog="email"]').getBoundingClientRect();
      return { sideBySide: detail.left > list.right, modal: document.querySelector('[data-dialog="email"]').matches(':modal'), badSourceLink: Boolean(document.querySelector('[data-email-content] a[href$="/undefined"]')) };
    });
    assertThat(desktopMail.sideBySide && !desktopMail.modal && !desktopMail.badSourceLink, 'desktop mail detail did not dock beside the list or contains an invalid source link');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('[data-dialog="email"] [data-email-content] .wb-detail-section').first().waitFor({ timeout: 20000 });
    const mobileMail = await page.evaluate(() => ({ modal: document.querySelector('[data-dialog="email"]').matches(':modal'), overflow: document.documentElement.scrollWidth > innerWidth }));
    assertThat(mobileMail.modal && !mobileMail.overflow, 'mobile mail detail is not modal or overflows the viewport');
    check('mail-responsive-detail', { desktopDocked: true, mobileModal: true, invalidSourceLinks: 0 });

    await page.goto(`${BASE_URL}/workbench?view=settings`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-open-workbench-preferences]').last().waitFor({ timeout: 20000 });
    await page.locator('.wb-appearance-card [data-open-workbench-preferences]').click();
    await page.locator('.wb-preferences-dialog[open]').waitFor();
    assertThat(await page.locator('.wb-preferences-dialog [name="theme_mode"]').count() === 1, 'settings appearance entry did not open the saved-preferences form');
    check('settings-appearance-entry', { existingPreferencesForm: true });

    assertThat(report.blockedExternalRequests.length === 0, 'browser attempted an external request');
    assertThat(report.pageErrors.length === 0, `browser page errors: ${report.pageErrors.join(' | ')}`);
    assertThat(report.consoleErrors.length === 0, `browser console errors: ${report.consoleErrors.join(' | ')}`);
    report.ok = true;
  } catch (error) {
    report.ok = false;
    report.error = error.message || String(error);
    try { await page.screenshot({ path: path.join(OUTPUT_DIR, 'ui-failure.png'), fullPage: true }); } catch { /* page may be unavailable */ }
    throw error;
  } finally {
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(REPORT_PATH, JSON.stringify(report, null, 2), 'utf8');
    await browser.close();
    console.log(JSON.stringify({ ok: report.ok, report: REPORT_PATH, checks: report.checks.map((item) => item.name), blockedExternalRequests: report.blockedExternalRequests.length }));
  }
}

main().catch((error) => {
  console.error(`Workbench UI acceptance failed: ${error.message || error}`);
  process.exitCode = 1;
});
