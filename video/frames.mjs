// Shared by render.mjs (16:9 promo) and ad.mjs (9:16 ad): grab a page frame by
// frame with headless Chromium, and measure a voice track's loudness per frame.
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: ['ignore', 'inherit', 'inherit'] });
  if (r.status !== 0) { console.error(`${cmd} failed (${r.status})`); process.exit(1); }
}

// Loudness 0..1 per video frame, fast attack / slower release, for mouths and waveforms.
export function envelope(file, fps) {
  const pcm = spawnSync('ffmpeg', ['-v', 'error', '-i', file, '-f', 's16le', '-ac', '1', '-ar', '24000', '-'],
    { maxBuffer: 1 << 29 }).stdout;
  const per = 24000 / fps, rms = [];
  for (let o = 0; o + 2 <= pcm.length; o += per * 2) {
    let sum = 0, n = 0;
    for (let k = o; k < Math.min(pcm.length - 1, o + per * 2); k += 2, n++) { const x = pcm.readInt16LE(k) / 32768; sum += x * x; }
    rms.push(Math.sqrt(sum / Math.max(1, n)));
  }
  const ref = [...rms].sort((a, b) => a - b)[Math.floor(rms.length * 0.95)] || 1;
  let lvl = 0;
  return rms.map(r => {
    const x = Math.max(0, Math.min(1, (r / ref - 0.08) / 0.92));
    lvl = x > lvl ? lvl + (x - lvl) * 0.7 : lvl + (x - lvl) * 0.35;
    return +lvl.toFixed(3);
  });
}

// Renders html (which must define window.ready / setup(tl) / render(t)) to an mp4 without sound.
export async function renderPage({ html, TL, width, height, out, quick = false }) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: quick ? 0.5 : 1 });
  // Behind an HTTPS proxy Chromium can't reach Google Fonts on its own; curl can.
  if (process.env.HTTPS_PROXY || process.env.https_proxy) {
    await page.route(/fonts\.(googleapis|gstatic)\.com/, route => {
      const url = route.request().url();
      const body = spawnSync('curl', ['-sSL', '-A', 'Mozilla/5.0 Chrome/130', url], { maxBuffer: 1 << 26 }).stdout;
      route.fulfill({ body, contentType: url.includes('css2') ? 'text/css' : 'font/woff2',
        headers: { 'access-control-allow-origin': '*' } });
    });
  }
  page.on('pageerror', e => console.error('page error:', e.message));
  await page.goto(pathToFileURL(html).href);
  await page.evaluate(async tl => { await window.ready; window.setup(tl); }, TL);

  const frames = Math.ceil(TL.total * TL.fps);
  const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(TL.fps), '-c:v', 'mjpeg',
    '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', quick ? '28' : '17', '-preset', quick ? 'veryfast' : 'slow',
    '-vf', `scale=${width}:${height}:flags=lanczos`, out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise(r => ff.on('close', r));
  const t0 = Date.now();
  for (let f = 0; f < frames; f++) {
    await page.evaluate(t => window.render(t), f / TL.fps);
    const jpg = await page.screenshot({ type: 'jpeg', quality: quick ? 80 : 95 });
    if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once('drain', r));
    if (f % TL.fps === 0) process.stdout.write(`\rframe ${f}/${frames}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end(); await done; await browser.close();
  console.log(`\rpicture: ${frames} frames in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
}
