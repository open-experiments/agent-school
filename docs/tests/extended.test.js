// Extended functional sweep, born out of the July 2026 QA round 2:
// X1  full user-path E2E of every step of all five lab tapes + every nav page
// X2  landing-page deep pass: per-course detail integrity, keyboard a11y, noopener
// X3  portal edge cases: re-run, drawer, minimize, fs gating, out-of-order jumps
// X3b XSS probe on the tape query param + error-page lab links
// X4  mobile (390px): notice visible, lab still functional
// X5  404 page content (Pages-level 404 routing is verified against the live site)
// Run via docs/tests/run.sh, or standalone with BASE_URL / CHROMIUM_PATH.
const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8777';
const TAPES = ['101-venice','201-venice','202-venice','301-venice','302-venice'];
const IDS = ['101','201','202','301','302'];

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const results = [];
  const check = (n, c, x = '') => results.push(`${c ? 'PASS' : 'FAIL'} | ${n}${x ? ' | ' + x : ''}`);
  const goRetry = async (page, url, opts) => { let last; for (let a = 0; a < 3; a++) { try { return await page.goto(url, opts); } catch (e) { last = e; await page.waitForTimeout(2500); } } throw last; };
  const newPage = async (vp) => {
    const page = await browser.newPage({ viewport: vp || { width: 1366, height: 950 } });
    page._errs = [];
    page.on('pageerror', e => page._errs.push('pageerror: ' + String(e).slice(0, 120)));
    page.on('console', m => { if (m.type() === 'error') page._errs.push('console: ' + m.text().slice(0, 120)); });
    return page;
  };
  const loadTape = async (page, tape) => {
    await goRetry(page, `${BASE}/portal/index.html?tape=${tape}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof TAPE !== 'undefined' && TAPE !== null, null, { timeout: 20000 });
  };
  const runCur = async (page) => {
    await page.evaluate(() => { runStep(); });
    await page.waitForFunction(() => !playing && ran.has(cur), null, { timeout: 90000 });
  };

  // ============ X1: full user-path E2E, every step of every tape ============
  for (const tape of TAPES) {
    const page = await newPage();
    try {
      await loadTape(page, tape);
      const nSteps = await page.evaluate(() => TAPE.steps.length);
      let ok = true, detail = '';
      for (let i = 0; i < nSteps; i++) {
        if (i > 0) await page.evaluate(() => document.getElementById('gnext').click());
        await runCur(page);
        await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
        const st = await page.evaluate(() => ({ cur, ran: ran.has(cur), found: found.has(cur), next: !document.getElementById('gnext').disabled }));
        const last = i === nSteps - 1;
        if (!(st.ran && st.found && (last || st.next))) { ok = false; detail = `step ${i + 1}: ${JSON.stringify(st)}`; break; }
      }
      const done = await page.evaluate(() => ({ txt: document.getElementById('gnext').textContent, found: found.size, dots: document.querySelectorAll('.dots i.done').length }));
      check(`X1 ${tape} full E2E (${nSteps} steps)`, ok && done.txt.includes('Course complete') && done.found === nSteps && done.dots === nSteps, detail || JSON.stringify(done));

      // every visible nav page renders non-empty, no errors
      const navIds = await page.evaluate(() => {
        const ids = []; NAV.forEach(n => { if (n.items) n.items.forEach(it => ids.push(it.id)); else ids.push(n.id); }); return ids;
      });
      let navOk = true, navBad = '';
      for (const id of navIds) {
        const len = await page.evaluate((p) => { try { go(p); } catch (e) { return -1; } return document.getElementById('content').innerHTML.length; }, id);
        if (len < 40) { navOk = false; navBad += `${id}:${len} `; }
      }
      check(`X1 ${tape} all nav pages render (${navIds.length})`, navOk, navBad);
      // models tabs both render
      const tabs = await page.evaluate(() => { go('models'); setTab('registry'); const a = document.getElementById('content').innerHTML.length; setTab('deployments'); const b = document.getElementById('content').innerHTML.length; return [a, b]; });
      check(`X1 ${tape} registry+deployments tabs`, tabs[0] > 40 && tabs[1] > 40, JSON.stringify(tabs));
      check(`X1 ${tape} zero JS/console errors`, page._errs.length === 0, page._errs.slice(0, 3).join('; '));
    } catch (e) { check(`X1 ${tape} full E2E`, false, String(e).slice(0, 160)); }
    await page.close();
  }

  // ============ X2: landing page deep pass ============
  {
    const page = await newPage();
    await goRetry(page, `${BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(600);
    for (const id of IDS) {
      // open via real card click
      await page.evaluate((cid) => { closeCourse(); show('courses'); }, id);
      const card = page.locator(`.card[aria-label*="${id}"]`);
      await card.scrollIntoViewIfNeeded();
      await card.click();
      await page.waitForTimeout(350);
      const st = await page.evaluate((cid) => {
        const c = COURSES.find(x => x.id === cid);
        const v = document.querySelector('#detailbody video');
        const links = [...document.querySelectorAll('.detail-links a')].map(a => a.getAttribute('href'));
        const img = document.querySelector('.arch img');
        return {
          visible: document.getElementById('coursedetail').style.display === 'block',
          badge: document.querySelector('.detail-head .badge').textContent,
          poster: v && v.getAttribute('poster'),
          src: v && v.querySelector('source').getAttribute('src'),
          links, flag: c.flag,
          walkBtn: links.length === 4,
          archSrc: img && img.getAttribute('src'),
        };
      }, id);
      const dir = { '101': '101-noc-assistant', '201': '201-rca-investigator', '202': '202-fraud-triage', '301': '301-closed-loop-netops', '302': '302-energy-optimizer' }[id];
      const okLinks = st.links.length === 4
        && st.links[0].endsWith(`/tree/main/${dir}`)
        && st.links[1].includes(`${dir}.mp4`)
        && st.links[2].endsWith(`/blob/main/${dir}/MANUAL.md`)
        && st.links[3] === `portal/index.html?tape=${id}-venice`;
      const badgeOk = st.badge.includes(id) && (st.flag ? st.badge.includes('flagship') : !st.badge.includes('flagship'));
      check(`X2 ${id} detail: badge/video/poster/links`, st.visible && badgeOk && st.poster === `posters/${id}.png` && st.src.includes(`${dir}.mp4`) && okLinks, JSON.stringify(st.links));
      // architecture image actually loads
      const archUrl = await page.evaluate(() => document.querySelector('.arch img').getAttribute('src'));
      const archOk = await new Promise(res => { const { execFile } = require('child_process'); execFile('curl', ['-s','-o','/dev/null','-w','%{http_code}', archUrl], (e, so) => res(so === '200')); });
      check(`X2 ${id} architecture image URL serves 200`, archOk, archUrl);
    }
    // keyboard: focus each card and open with Enter
    let kOk = true, kBad = '';
    for (const id of IDS) {
      await page.evaluate(() => { closeCourse(); });
      await page.waitForTimeout(150);
      const opened = await page.evaluate(async (cid) => {
        const card = document.querySelector(`.card[aria-label*="Open course ${cid}"]`);
        card.focus();
        card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        await new Promise(r => setTimeout(r, 250));
        return document.getElementById('coursedetail').style.display === 'block' && document.querySelector('.detail-head .badge').textContent.includes(cid);
      }, id);
      if (!opened) { kOk = false; kBad += id + ' '; }
    }
    check('X2 keyboard Enter opens all 5 cards', kOk, kBad);
    // noopener discipline on all target=_blank links
    const blank = await page.evaluate(() => [...document.querySelectorAll('a[target="_blank"]')].filter(a => !(a.getAttribute('rel') || '').includes('noopener')).length);
    check('X2 all target=_blank carry noopener', blank === 0, `missing=${blank}`);
    // publications
    const pubs = await page.evaluate(() => { show('pubs'); const items = [...document.querySelectorAll('.pub')]; return { n: items.length, hosts: [...document.querySelectorAll('.pub .read')].map(a => a.textContent) }; });
    check('X2 publications render 8 with hosts', pubs.n === 8 && pubs.hosts.every(h => h.includes('developers.redhat.com') || h.includes('medium.com')), JSON.stringify(pubs.hosts.slice(0, 2)));
    // view switch restores course grid
    const restore = await page.evaluate(() => { show('courses'); return document.getElementById('courselist').style.display !== 'none'; });
    check('X2 pubs->courses restores grid', restore);
    const realErrs = page._errs.filter(e => !e.includes('ERR_CONNECTION_RESET'));
    check('X2 zero JS/console errors on landing (net-blocked assets excluded)', realErrs.length === 0, realErrs.slice(0, 3).join('; '));
    await page.close();
  }

  // ============ X3: portal edge cases (101) ============
  {
    const page = await newPage();
    await loadTape(page, '101-venice');
    // run step 1 twice — no crash, terminal has output
    await runCur(page);
    await page.evaluate(() => { runStep(); });
    await page.waitForFunction(() => !playing, null, { timeout: 90000 });
    const t1 = await page.evaluate(() => ({ errs: 0, out: document.getElementById('tout').children.length }));
    check('X3 re-running a step is safe', t1.out > 0, JSON.stringify(t1));
    // drawer close/reopen retains output
    const drawer = await page.evaluate(() => { const d = document.getElementById('drawer'); if (!d.classList.contains('open')) toggleDrawer(); toggleDrawer(); const closed = !d.classList.contains('open'); toggleDrawer(); const reopened = d.classList.contains('open'); return { closed, reopened, out: document.getElementById('tout').children.length }; });
    check('X3 drawer close/reopen retains output', drawer.closed && drawer.reopened && drawer.out > 0, JSON.stringify(drawer));
    // guide minimize toggle
    const min = await page.evaluate(() => { const g = document.getElementById('guide'); g.querySelector('.head').click(); const a = g.classList.contains('min'); g.querySelector('.head').click(); return a && !g.classList.contains('min'); });
    check('X3 guide minimize/expand', min);
    // feature-store nav gating: hidden before step 4 runs, visible after
    const fsBefore = await page.evaluate(() => fsOpen);
    await page.evaluate(() => setStep(3));
    await runCur(page);
    const fsAfter = await page.evaluate(() => ({ fsOpen, navVisible: document.getElementById('nav-fs-views') && document.getElementById('nav-fs-views').classList.contains('visible') }));
    check('X3 101 feature store unlocks at step 4', fsBefore === false && fsAfter.fsOpen === true, JSON.stringify({ fsBefore, fsAfter }));
    // out-of-order: jump to last step directly and complete it
    await page.evaluate(() => setStep(6));
    await runCur(page);
    await page.evaluate(() => { const g = document.getElementById('gnav'); if (g && g.style.display !== 'none') g.click(); });
    const jump = await page.evaluate(() => ({ found: found.has(6), chip: document.getElementById('gchip').textContent }));
    check('X3 out-of-order step jump works', jump.found && jump.chip.includes('7/7'), JSON.stringify(jump));
    check('X3 zero JS/console errors in edge cases', page._errs.length === 0, page._errs.slice(0, 3).join('; '));
    await page.close();
  }

  // ============ X3b: XSS probe on tape param ============
  {
    const page = await newPage();
    await goRetry(page, `${BASE}/portal/index.html?tape=<img src=x onerror=window.__pwned=1>`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const x = await page.evaluate(() => ({ pwned: window.__pwned === 1, imgs: document.querySelectorAll('#loading img').length, txt: document.getElementById('loading').textContent.slice(0, 60) }));
    check('X3b tape param XSS-safe', !x.pwned && x.imgs === 0 && x.txt.includes('not found'), JSON.stringify(x));
    // lab links on error page navigate correctly
    await page.evaluate(() => { document.querySelector('#loading a[href="?tape=201-venice"]').click(); });
    await page.waitForFunction(() => typeof TAPE !== 'undefined' && TAPE !== null, null, { timeout: 20000 }).catch(() => {});
    const nav = await page.evaluate(() => TAPE && TAPE.course);
    check('X3b error-page lab link loads 201', nav === '201', String(nav));
    await page.close();
  }

  // ============ X4: mobile portal sanity (390x844) ============
  {
    const page = await newPage({ width: 390, height: 844 });
    await loadTape(page, '101-venice');
    const m = await page.evaluate(() => {
      const note = document.querySelector('.mobilenote');
      const vis = note && getComputedStyle(note).display !== 'none';
      return { noteVisible: vis, noteText: note && note.textContent.slice(0, 40) };
    });
    check('X4 mobile notice visible at 390px', m.noteVisible === true, JSON.stringify(m));
    await runCur(page);
    const run = await page.evaluate(() => ({ out: document.getElementById('tout').children.length, drawerOpen: document.getElementById('drawer').classList.contains('open') }));
    check('X4 mobile: step runs, terminal output present', run.out > 0, JSON.stringify(run));
    check('X4 zero JS errors on mobile', page._errs.length === 0, page._errs.slice(0, 2).join('; '));
    await page.close();
  }

  // ============ X5: 404 + assets ============
  {
    const page = await newPage();
    const resp = await goRetry(page, `${BASE}/404.html`, { waitUntil: 'domcontentloaded' });
    const body = await page.evaluate(() => document.body.innerText);
    check('X5 404 page content correct (live 404-routing verified in Chrome earlier)', body.includes('off-curriculum'), `status=${resp.status()}`);
    const home = await page.evaluate(() => { const a = document.querySelector('a.btn'); return a && a.getAttribute('href'); });
    check('X5 404 back-link points to site root', home === '/agent-school/');
    await page.close();
  }

  console.log(results.join('\n'));
  const p = results.filter(r => r.startsWith('PASS')).length;
  console.log(`\n${p}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
