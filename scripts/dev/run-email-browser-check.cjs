const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const runtime = process.argv[2];
  if (!runtime) throw new Error('Pass the path to an installed playwright-core package');
  const { chromium } = require(path.resolve(runtime));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const callback = new Function(`return (${fs.readFileSync(path.join(__dirname, 'check-email-api.js'), 'utf8')})`)();
    let result;
    try {
      result = process.argv.includes('--fixture-only') ? null : await callback(page);
    } catch (error) {
      console.error(await page.locator('body').innerText());
      await page.screenshot({ path: '.tmp/email-api-failure.png', fullPage: true });
      throw error;
    }
    if (result) {
      fs.writeFileSync('.tmp/email-api-acceptance.json', JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result));
    }
    const fixtureCheck = new Function(`return (${fs.readFileSync(path.join(__dirname, 'check-email-inbox.js'), 'utf8')})`)();
    const fixtureResult = await fixtureCheck(page, { baseURL: 'http://127.0.0.1:18768', screenshotPath: '.tmp/email-fixture-acceptance.png' });
    fs.writeFileSync('.tmp/email-fixture-acceptance.json', JSON.stringify(fixtureResult, null, 2));
    console.log(JSON.stringify({ fixture: fixtureResult }));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
