const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8777';
(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const results = [];
  const check = (n, c, x = '') => results.push(`${c ? 'PASS' : 'FAIL'} | ${n}${x ? ' | ' + x : ''}`);
  const load = async (tape) => {
    const page = await browser.newPage();
    await page.goto(`${BASE}/portal/index.html?tape=${tape}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => TAPE !== null, null, { timeout: 10000 });
    return page;
  };
  const runCur = async (page) => {
    await page.evaluate(() => { runStep(); });
    await page.waitForFunction(() => !playing && ran.has(cur), null, { timeout: 60000 });
  };
  const projectsText = async (page) => {
    await page.evaluate(() => go('projects'));
    return page.evaluate(() => document.getElementById('content').innerText);
  };

  // 101: after step 4, all object kinds render properly
  {
    const page = await load('101-venice');
    await page.evaluate(() => setStep(3));
    await runCur(page);
    const txt = await projectsText(page);
    check('F13 101 no null/ entries', !txt.includes('null/'));
    for (const want of ['ConfigMap/mlflow-tracking', 'ConfigMap/feature-store-client', 'ServiceAccount/noc-assistant', 'CronJob/noc-sweep', 'PersistentVolumeClaim/', 'FeatureStore/fivegprod'])
      check(`F13 101 shows ${want}`, txt.includes(want));
    await page.close();
  }

  // 301: agents render as Deployment/<name>
  {
    const page = await load('301-venice');
    await page.evaluate(() => setStep(3));
    await runCur(page);
    const txt = await projectsText(page);
    check('F13 301 no null/ entries', !txt.includes('null/'));
    for (const want of ['Deployment/diagnostic-agent', 'Deployment/planning-agent', 'Deployment/loop-state', 'Deployment/mcp-playbook'])
      check(`F13 301 shows ${want}`, txt.includes(want));
    await page.close();
  }

  // 302: sa + deployments render kinds
  {
    const page = await load('302-venice');
    await page.evaluate(() => setStep(3));
    await runCur(page);
    const txt = await projectsText(page);
    check('F13 302 no null/ entries', !txt.includes('null/'));
    check('F13 302 shows ServiceAccount/energy-optimizer', txt.includes('ServiceAccount/energy-optimizer'));
    check('F13 302 shows Deployment/scorer-mcp', txt.includes('Deployment/scorer-mcp'));
    await page.close();
  }

  // 201: full E2E regression + kinds
  {
    const page = await load('201-venice');
    for (let i = 0; i < 3; i++) {
      if (i > 0) await page.evaluate(() => document.getElementById('gnext').click());
      await runCur(page);
      await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
    }
    const done = await page.evaluate(() => document.getElementById('gnext').textContent);
    check('regression 201 full E2E complete', done.includes('Course complete'), done);
    const txt = await projectsText(page);
    check('F13 201 no null/ entries', !txt.includes('null/'));
    check('F13 201 shows Deployment/rca-rag', txt.includes('Deployment/rca-rag'));
    await page.close();
  }

  // errors + bogus tape regression
  {
    const page = await browser.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(`${BASE}/portal/index.html?tape=nope`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1000);
    const txt = await page.evaluate(() => document.getElementById('loading').textContent);
    check('regression bogus tape', txt.includes('not found'), txt);
    check('no page errors', errs.length === 0, errs.join('; '));
    await page.close();
  }

  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
