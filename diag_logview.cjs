const { chromium } = require('playwright');

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 3 });
  await p.goto('http://127.0.0.1:8092/', { waitUntil: 'load' });
  await p.waitForTimeout(2500);

  const info = await p.evaluate(() => {
    const row = document.querySelector('.row');
    const lv = row.querySelector('.lv-tag');
    const bz = row.querySelector('.bz');
    const cs = getComputedStyle(lv);
    return {
      rows: document.querySelectorAll('.row').length,
      lvText: lv.textContent, lvBg: cs.backgroundColor, lvColor: cs.color,
      lvW: cs.width, lvBox: lv.getBoundingClientRect().toJSON(),
      bzText: bz.textContent, bzBg: getComputedStyle(bz).backgroundColor,
      sidebar: [...document.querySelectorAll('.item')].map(e => ({
        k: e.dataset.kind, id: e.dataset.id,
        n: e.querySelector('.n').textContent,
        badge: e.querySelector('.badge').textContent,
        badgeShown: e.querySelector('.badge').classList.contains('show'),
      })),
    };
  });
  console.log(JSON.stringify(info, null, 1));

  // 截取左侧栏 + 前几行，放大看细节
  await p.locator('aside').screenshot({ path: 'D:/AI/tools/pmdocker/work/logview/crop-sidebar.png' });
  const rows = p.locator('#stream');
  await rows.screenshot({ path: 'D:/AI/tools/pmdocker/work/logview/crop-stream.png', clip: undefined });
  await b.close();
})();
