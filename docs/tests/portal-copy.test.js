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

  // F2: 101 step 6 guide copy no longer promises visible runs
  {
    const page = await load('101-venice');
    await page.evaluate(() => setStep(5));
    const body = await page.evaluate(() => document.getElementById('gbody').innerText);
    check('F2 101 s6 Expect mentions Traces-not-runs', body.includes('Traces tab') && body.includes('runs list stays empty'));
    check('F2 101 s6 old promise gone', !body.includes('the run appears with full LLM traces'));
    check('F2 101 s6 names right experiment', body.includes('101-noc-assistant'));
    // dashboard note after running
    await runCur(page);
    await page.evaluate(() => go('experiments'));
    const note = await page.evaluate(() => document.getElementById('content').innerText);
    check('F2 101 s6 dashboard note honest', note.includes('Traces are not runs'));
    // full finish: step 7 still completes the course
    await page.evaluate(() => document.getElementById('gnext').click());
    await runCur(page);
    await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
    const done = await page.evaluate(() => document.getElementById('gnext').textContent);
    check('regression 101 completes after copy edits', done.includes('Course complete'), done);
    // F10 footnotes visible in guide
    await page.evaluate(() => setStep(4));
    const s5 = await page.evaluate(() => document.getElementById('gbody').innerText);
    check('F10a 101 s5 timestamp footnote', s5.includes('January 2025'));
    await page.evaluate(() => setStep(6));
    const s7 = await page.evaluate(() => document.getElementById('gbody').innerText);
    check('F10b 101 s7 f1 footnote', s7.includes('rate_gap'));
    await page.close();
  }

  // F2: 201 steps 2-3 copy + full E2E regression
  {
    const page = await load('201-venice');
    for (let i = 0; i < 3; i++) {
      if (i > 0) await page.evaluate(() => document.getElementById('gnext').click());
      const body = await page.evaluate(() => document.getElementById('gbody').innerText);
      if (i === 1) {
        check('F2 201 s2 Expect honest', body.includes('Traces tab') && body.includes('runs list stays empty'));
        check('F2 201 s2 old promise gone', !body.includes('now shows the run with traces'));
      }
      if (i === 2) check('F2 201 s3 Expect honest', body.includes('Traces tab'));
      await runCur(page);
      await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
    }
    const done = await page.evaluate(() => document.getElementById('gnext').textContent);
    check('regression 201 completes after copy edits', done.includes('Course complete'), done);
    // kimi row survived regen
    await page.evaluate(() => { go('models'); setTab('deployments'); });
    const dep = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('regression 201 kimi row after regen', dep.includes('kimi-linear-48b-a3b'));
    await page.close();
  }

  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
