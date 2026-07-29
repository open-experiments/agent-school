const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8777';
(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const results = [];
  const check = (n, c, x = '') => results.push(`${c ? 'PASS' : 'FAIL'} | ${n}${x ? ' | ' + x : ''}`);
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(`${BASE}/portal/index.html?tape=101-venice`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => TAPE !== null, null, { timeout: 10000 });
  const runCur = async () => {
    await page.evaluate(() => { runStep(); });
    await page.waitForFunction(() => !playing && ran.has(cur), null, { timeout: 90000 });
  };
  // Full 7-step E2E on the regenerated tape
  for (let i = 0; i < 7; i++) {
    if (i > 0) await page.evaluate(() => document.getElementById('gnext').click());
    await runCur();
    await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
  }
  const done = await page.evaluate(() => ({ txt: document.getElementById('gnext').textContent, found: found.size }));
  check('101 regen tape full E2E complete', done.txt.includes('Course complete') && done.found === 7, JSON.stringify(done));
  // F13: projects clean
  await page.evaluate(() => go('projects'));
  const proj = await page.evaluate(() => document.getElementById('content').innerText);
  check('projects no null/, kinds present', !proj.includes('null/') && proj.includes('ConfigMap/mlflow-tracking'));
  // F8: kimi row (standardized isvc_slim shape)
  await page.evaluate(() => { go('models'); setTab('deployments'); });
  const dep = await page.evaluate(() => document.getElementById('content').innerText);
  check('kimi row renders from standardized asset', dep.includes('kimi-linear-48b-a3b') && dep.includes('custom-vllm-tp1') && dep.includes('Ready'));
  // F5: sweep visible only post-run (it ran) — sanity that reveal survived
  await page.evaluate(() => go('experiments'));
  const xp = await page.evaluate(() => document.getElementById('content').innerText);
  check('sweep experiment present after full run', xp.includes('5gprod-anomaly-sweep'));
  // terminal replayed the curated ray log
  const term = await page.evaluate(() => document.getElementById('tout').innerText);
  check('curated ray-submit log replayed', term.includes('Job submission server address') && term.includes("succeeded"));
  check('no page errors', errs.length === 0, errs.join('; '));
  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
