const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const shots = [
    { name: 'desktop', w: 1440, h: 900 },
    { name: 'mobile', w: 390, h: 844 },
  ];
  for (const s of shots) {
    const page = await browser.newPage({ viewport: { width: s.w, height: s.h }, deviceScaleFactor: 2 });
    await page.goto('http://127.0.0.1:8092/', { waitUntil: 'load' });
    await page.waitForTimeout(2600);
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    await page.screenshot({ path: `D:/AI/tools/pmdocker/work/logview/shot-${s.name}.png` });
    console.log(`${s.name} ok, rows=`, await page.locator('.row').count(), errs.length ? 'JS错误:' + errs.join(';') : '无JS错误');
    await page.close();
  }
  await browser.close();
})();
