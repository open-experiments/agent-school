// Print render.html to a 1920x1080-per-page PDF and one PNG per slide (Playwright + Chromium).
// node render.js <render.html> <out.pdf> <png-dir>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [, , htmlPath, pdfPath, pngDir] = process.argv;
  fs.mkdirSync(pngDir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(htmlPath), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  await page.pdf({ path: pdfPath, width: '1920px', height: '1080px', printBackground: true, preferCSSPageSize: true });
  const sections = await page.$$('section');
  for (let i = 0; i < sections.length; i++) {
    await sections[i].screenshot({ path: path.join(pngDir, `slide-${String(i + 1).padStart(2, '0')}.png`) });
  }
  console.log(`rendered ${sections.length} slides`);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
