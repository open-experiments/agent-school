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

  // ---- F4: "Take me there" switches to Deployments tab (202 step 6, 301 step 6, 302 step 3)
  for (const [tape, idx] of [['202-venice', 5], ['301-venice', 5], ['302-venice', 2]]) {
    const page = await load(tape);
    await page.evaluate(i => setStep(i), idx);
    await runCur(page);
    await page.evaluate(() => document.getElementById('gnav').click());
    const st = await page.evaluate(() => ({ page: page, tab: modelsTab, found: found.has(cur), next: !document.getElementById('gnext').disabled }));
    check(`F4 ${tape} step ${idx + 1} gnav -> deployments tab + verified`, st.page === 'models' && st.tab === 'deployments' && st.found && st.next, JSON.stringify(st));
    // F8 while here: kimi row present in deployments table
    const html = await page.evaluate(() => document.getElementById('content').innerHTML);
    check(`F8 ${tape} deployments shows course LLM`, html.includes('kimi-linear-48b-a3b') && html.includes('the course LLM every agent calls'));
    await page.close();
  }

  // ---- F8 on 201 (no deployments step): open Models > Deployments manually
  {
    const page = await load('201-venice');
    await page.evaluate(() => { go('models'); setTab('deployments'); });
    const html = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('F8 201-venice deployments shows course LLM', html.includes('kimi-linear-48b-a3b'));
    // and Projects course-objects must NOT list kimi
    await page.evaluate(() => { setStep(0); });
    await runCur(page);
    await page.evaluate(() => go('projects'));
    const proj = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('F8 201 projects list excludes kimi', !proj.includes('kimi'));
    await page.close();
  }

  // ---- F5: 101 sweep experiment hidden at step 6, revealed after step 7 runs
  {
    const page = await load('101-venice');
    await page.evaluate(() => setStep(5)); // step 6, prior steps auto-done
    await runCur(page);                    // run step 6 (ask the agent)
    await page.evaluate(() => go('experiments'));
    let html = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('F5 sweep hidden at step 6', !html.includes('5gprod-anomaly-sweep') && html.includes('101-noc-assistant'), '');
    await page.evaluate(() => document.getElementById('gnext').click()); // -> step 7
    await runCur(page);                    // run the sweep
    await page.evaluate(() => go('experiments'));
    html = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('F5 sweep visible after step 7 run', html.includes('5gprod-anomaly-sweep') && html.includes('ray-contamination-sweep'));
    // 101 kimi row still there (tape untouched apart from reveal)
    await page.evaluate(() => { go('models'); setTab('deployments'); });
    const dep = await page.evaluate(() => document.getElementById('content').innerHTML);
    check('F8 101 deployments still shows course LLM', dep.includes('kimi-linear-48b-a3b'));
    await page.close();
  }

  // ---- Regression: 201 full E2E to Course complete
  {
    const page = await load('201-venice');
    for (let i = 0; i < 3; i++) {
      if (i > 0) await page.evaluate(() => document.getElementById('gnext').click());
      await runCur(page);
      await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
    }
    const done = await page.evaluate(() => ({ txt: document.getElementById('gnext').textContent, found: found.size }));
    check('regression 201 full E2E complete', done.txt.includes('Course complete') && done.found === 3, JSON.stringify(done));
    await page.close();
  }

  // ---- Regression: registry-target step still auto-verifies via gnav (301 step 5)
  {
    const page = await load('301-venice');
    await page.evaluate(() => setStep(4));
    await runCur(page);
    await page.evaluate(() => document.getElementById('gnav').click());
    const st = await page.evaluate(() => ({ tab: modelsTab, found: found.has(4) }));
    check('regression 301 step5 registry tab verified', st.tab === 'registry' && st.found, JSON.stringify(st));
    await page.close();
  }

  // ---- Regression: bogus tape + console errors
  {
    const page = await browser.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(`${BASE}/portal/index.html?tape=nope`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const txt = await page.evaluate(() => document.getElementById('loading').textContent);
    check('regression bogus tape message', txt.includes('not found'), txt);
    check('no page errors across suite', errs.length === 0, errs.join('; '));
    await page.close();
  }

  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
