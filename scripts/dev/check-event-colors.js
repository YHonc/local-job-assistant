async (page, options = {}) => {
  const baseURL = options.baseURL || 'http://127.0.0.1:18766';
  const screenshotPath = options.screenshotPath || 'output/preview/event-status-colors.png';
  const statuses = ['unmatched', 'suggested', 'confirmed', 'dismissed'];
  const fixtures = statuses.map((status, i) => ({id:i+1, association_status:status, event_type:'INTERVIEW', company_name:'示例公司', job_title:'界面检查岗位', ai_summary:'用于检查状态颜色的模拟事件。', candidates:[], application_id:null, confidence:0.9}));
  await page.route('**/api/**', route => route.fulfill({json:route.request().url().includes('/recruitment-events') ? fixtures : []}));
  await page.route('**/static/**/*.js*', route => route.request().url().includes('/events.js') ? route.continue() : route.fulfill({contentType:'application/javascript',body:''}));
  const headers = [];
  for (const path of ['/', '/events', '/notifications', '/settings']) {
    await page.goto(baseURL+path);
    const count = await page.locator('main > header .page-path').count();
    if (count) throw new Error('Extra header remains: '+path);
    headers.push({path,title:await page.locator('h1').textContent(),extraHeaderCount:count});
  }
  await page.goto(baseURL+'/events');
  await page.locator('.association-status').last().waitFor();
  const checks = [];
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => document.documentElement.dataset.theme=theme, theme);
    for (const width of [1189, 700, 390]) {
      await page.setViewportSize({width,height:912});
      const result = await page.evaluate(() => ({overflow:document.documentElement.scrollWidth>innerWidth,badges:[...document.querySelectorAll('.association-status')].map(el=>({status:el.dataset.associationStatus,label:el.textContent,color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,width:el.getBoundingClientRect().width}))}));
      if (result.overflow || new Set(result.badges.map(b=>b.color)).size!==4) throw new Error('Layout/color check failed '+theme+' '+width);
      checks.push({theme,width,...result});
    }
  }
  await page.evaluate(() => document.documentElement.dataset.theme='light');
  await page.setViewportSize({width:1189,height:912});
  await page.screenshot({path:screenshotPath,fullPage:true});
  return {headers,checks};
}
