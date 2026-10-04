import { chromium } from 'playwright';
import { spawnSync } from 'node:child_process';
const b = await chromium.launch();
const shots = [];
for (const [aspect, theme, w, h] of [['v','dark',1080,1920],['v','light',1080,1920],['h','dark',1920,1080],['h','light',1920,1080]]) {
  const p = await b.newPage({viewport:{width:w,height:h}});
  p.on('pageerror', e => console.log('ERR', e.message));
  await p.route(/fonts\.(googleapis|gstatic)\.com/, r => { const u=r.request().url(); r.fulfill({body:spawnSync('curl',['-sSL','-A','Mozilla/5.0 Chrome/130',u],{maxBuffer:1<<26}).stdout, contentType:u.includes('css2')?'text/css':'font/woff2', headers:{'access-control-allow-origin':'*'}}); });
  await p.goto('file://'+process.cwd()+'/combo.html'); await p.evaluate(async a=>{await window.ready; window.setup(a);}, {aspect, theme});
  for (const t of (process.argv[2]||'3,8.3,11,21,28,42,52,66,76').split(',').map(Number)) {
    await p.evaluate(t=>window.render(t), t); const f=`build/combo/st_${aspect}_${theme}_${t}.jpg`;
    await p.screenshot({path:f, type:'jpeg', quality:60}); shots.push(f);
  }
  await p.close();
}
await b.close();
