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
  const expect = { '101': '▶ 6:27', '201': '▶ 4:36', '202': '▶ 5:52', '301': '▶ 7:22', '302': '▶ 5:38' };
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
  check('F1 walkthrough button 301', walk.includes('7:22'), walk);

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

  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  check('no page errors', errors.length === 0, errors.join('; '));

  console.log(results.join('\n'));
  console.log(`\n${results.filter(r => r.startsWith('PASS')).length}/${results.length} passed`);
  await browser.close();
  process.exit(results.some(r => r.startsWith('FAIL')) ? 1 : 0);
})();
