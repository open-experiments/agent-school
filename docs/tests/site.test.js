const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8777';
(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const page = await browser.newPage();
  const results = [];
  const check = (name, cond, extra = '') => results.push(`${cond ? 'PASS' : 'FAIL'} | ${name}${extra ? ' | ' + extra : ''}`);

  await page.goto(`${BASE}/index.html`, { waitUntil: 'domcontentloaded' });

  // F1: card durations
  const lens = await page.$$eval('#cardgrid .card .len', els => els.map(e => e.textContent.trim()));
  const expect = { '101': '▶ 6:29', '201': '▶ 4:36', '202': '▶ 5:52', '301': '▶ 8:55', '302': '▶ 7:09' };
  const ids = await page.$$eval('#cardgrid .card .badge', els => els.map(e => e.textContent.trim().split(' ')[0]));
  ids.forEach((id, i) => check(`F1 duration ${id}`, lens[i] === expect[id], `got "${lens[i]}"`));

  // Open 301 detail
  await page.click('#cardgrid .card:nth-child(4)');
  await page.waitForTimeout(900);

  // F7: flagship badge on detail
  const badge = await page.$eval('#detailbody .badge', e => e.textContent.trim());
  check('F7 detail badge shows flagship', badge === '301 · flagship', `got "${badge}"`);

  // F6: manual link uses blob
  const manual = await page.$eval('#detailbody a:has-text("Course Manual")', e => e.href);
  check('F6 Course Manual blob URL', manual === 'https://github.com/open-experiments/agent-school/blob/main/301-closed-loop-netops/MANUAL.md', manual);

  // walkthrough button duration matches F1
  const walk = await page.$eval('#detailbody a:has-text("Open walkthrough")', e => e.textContent.trim());
  check('F1 walkthrough button 301', walk.includes('8:55'), walk);

  // F9: page scrolled to detail (not top)
  const scrollY = await page.evaluate(() => window.scrollY);
  const detailTop = await page.evaluate(() => document.getElementById('coursedetail').getBoundingClientRect().top);
  check('F9 scrolled into detail view', scrollY > 200 && detailTop >= 0 && detailTop < 200, `scrollY=${scrollY} detailTop=${Math.round(detailTop)}`);

  // back button restores list
  await page.click('#detailbody .back');
  await page.waitForTimeout(400);
  const listVisible = await page.$eval('#courselist', e => getComputedStyle(e).display !== 'none');
  check('back button restores course list', listVisible);

  // regression: publications view + all 5 details render, lab links intact
  for (const id of ['101', '201', '202', '301', '302']) {
    await page.evaluate(i => { window.openCourse(i); }, id);
    const links = await page.$$eval('#detailbody .detail-links a', els => els.map(e => e.textContent.trim()));
    check(`regression ${id} detail has 4 links`, links.length === 4, links.join(' ~ '));
    const lab = await page.$eval('#detailbody a:has-text("interactive lab")', e => e.href);
    check(`regression ${id} lab link`, lab.endsWith(`portal/index.html?tape=${id}-venice`), lab);
    await page.evaluate(() => window.closeCourse());
  }
  await page.evaluate(() => window.show('pubs'));
  const pubs = await page.$$eval('#publist .pub', els => els.length);
  check('regression publications render', pubs === 8, `count=${pubs}`);

  // R1-R6: roadmap view (November 2026)
  await page.evaluate(() => window.show('roadmap'));
  await page.waitForTimeout(300);
  const rmActive = await page.evaluate(() => document.getElementById('roadmap').classList.contains('active') && document.getElementById('nav-roadmap').classList.contains('active') && !document.getElementById('courses').classList.contains('active'));
  check('R1 roadmap view and nav active', rmActive);
  const rIds = await page.$$eval('#rgrid .rcard .badge', els => els.map(e => e.textContent.trim()));
  check('R2 three proposed 4xx cards', rIds.join(',') === '401 · proposed,402 · proposed,403 · proposed', rIds.join(','));
  const rLinks = await page.$$eval('#rgrid .rcard a.foot', els => els.map(e => e.href));
  check('R2 cards link to RoadMap.md sections', rLinks.length === 3 && rLinks.every(h => h.startsWith('https://github.com/open-experiments/agent-school/blob/main/docs/RoadMap.md#40')), rLinks.join(' ~ '));
  const steps = await page.$$eval('#steps .step', els => els.length);
  check('R3 five gated steps', steps === 5, `count=${steps}`);
  const v0 = await page.evaluate(() => ({ src: document.getElementById('slideimg').getAttribute('src'), cnt: document.getElementById('slidecnt').textContent }));
  await page.click('#viewer button[aria-label="Next slide"]');
  const v1 = await page.evaluate(() => ({ src: document.getElementById('slideimg').getAttribute('src'), cnt: document.getElementById('slidecnt').textContent }));
  await page.click('#viewer button[aria-label="Previous slide"]');
  await page.click('#viewer button[aria-label="Previous slide"]');
  const v2 = await page.evaluate(() => ({ src: document.getElementById('slideimg').getAttribute('src'), cnt: document.getElementById('slidecnt').textContent }));
  check('R4 slide viewer starts at 1 of 9', v0.src === 'roadmap/web/slide-01.jpg' && v0.cnt === '1 / 9', JSON.stringify(v0));
  check('R4 slide viewer next/prev wraps', v1.src === 'roadmap/web/slide-02.jpg' && v1.cnt === '2 / 9' && v2.src === 'roadmap/web/slide-09.jpg' && v2.cnt === '9 / 9', JSON.stringify([v1, v2]));
  const assets = await page.evaluate(async () => {
    const urls = ['roadmap/agent-school-roadmap-wg-kickoff-2026-11.pdf', 'roadmap/agent-school-roadmap-wg-kickoff-2026-11.pptx'];
    for (let i = 1; i <= 9; i++) urls.push(`roadmap/web/slide-${String(i).padStart(2, '0')}.jpg`);
    const out = [];
    for (const u of urls) { const r = await fetch(u, { method: 'HEAD' }); out.push(`${u}:${r.status}`); }
    return out;
  });
  check('R5 deck PDF, PPTX and 9 slide images served', assets.every(a => a.endsWith(':200')), assets.filter(a => !a.endsWith(':200')).join(' ') || `${assets.length} ok`);
  const later = await page.$$eval('#later li', els => els.length);
  check('R6 unscheduled candidates listed', later === 6, `count=${later}`);
  const strip = await page.evaluate(() => { window.show('courses'); return document.getElementById('nextitems').textContent.includes('401') && document.getElementById('courselist').style.display !== 'none'; });
  check('R6 courses view restored with next-strip', strip);

  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  check('no page errors', errors.length === 0, errors.join('; '));

  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
