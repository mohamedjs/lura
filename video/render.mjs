// Frame-grabs scenes.html with headless Chromium, then muxes narration + music.
//   node render.mjs [--footage ~/Downloads/clip.mp4 --footage-start 3] [--out out/lura.mp4]
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, 'build');
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const OUT = resolve(arg('--out', join(HERE, 'out', 'lura.mp4')));
const FOOTAGE = arg('--footage');
const FOOT_START = Number(arg('--footage-start', '0'));
const QUICK = process.argv.includes('--preview');   // half-res, fast check

function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: ['ignore', 'inherit', 'inherit'] });
  if (r.status !== 0) { console.error(`${cmd} failed (${r.status})`); process.exit(1); }
}

const tlPath = join(BUILD, 'timeline.json');
if (!existsSync(tlPath)) { console.error('No build/timeline.json — run `python3 voice.py` first.'); process.exit(1); }
const TL = JSON.parse(readFileSync(tlPath, 'utf8'));
mkdirSync(dirname(OUT), { recursive: true });

if (FOOTAGE) {
  // Chromium here has no H.264, so hand it plain JPEG frames sized to the window.
  const dir = join(BUILD, 'footage');
  rmSync(dir, { recursive: true, force: true }); mkdirSync(dir, { recursive: true });
  const s4 = TL.scenes[3];
  run('ffmpeg', ['-v', 'error', '-ss', String(FOOT_START), '-i', resolve(FOOTAGE), '-t', String(s4.dur + 0.5),
    '-vf', `fps=${TL.fps},scale=1000:514:force_original_aspect_ratio=increase,crop=1000:514`,
    '-q:v', '3', join(dir, 'f%04d.jpg')]);
  TL.footage = { dir: 'build/footage', fps: TL.fps, count: readdirSync(dir).length };
  console.log(`footage: ${TL.footage.count} frames from ${FOOTAGE}`);
}

// Loudness envelope per frame for each narration line → drives the mouth + waveform.
for (const s of TL.scenes) {
  const wav = s.wav && join(BUILD, s.wav);
  if (!wav || !existsSync(wav)) continue;
  const pcm = spawnSync('ffmpeg', ['-v', 'error', '-i', wav, '-f', 's16le', '-ac', '1', '-ar', '24000', '-'],
    { maxBuffer: 1 << 28 }).stdout;
  const per = 24000 / TL.fps, rms = [];
  for (let o = 0; o + 2 <= pcm.length; o += per * 2) {
    let sum = 0, n = 0;
    for (let k = o; k < Math.min(pcm.length - 1, o + per * 2); k += 2, n++) { const x = pcm.readInt16LE(k) / 32768; sum += x * x; }
    rms.push(Math.sqrt(sum / Math.max(1, n)));
  }
  const ref = [...rms].sort((a, b) => a - b)[Math.floor(rms.length * 0.95)] || 1;
  let lvl = 0;
  s.env = rms.map(r => {                                   // fast attack, slower release
    const x = Math.max(0, Math.min(1, (r / ref - 0.08) / 0.92));
    lvl = x > lvl ? lvl + (x - lvl) * 0.7 : lvl + (x - lvl) * 0.35;
    return +lvl.toFixed(3);
  });
}

// ── picture ────────────────────────────────────────────────────────────────
const scale = QUICK ? 0.5 : 1;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: scale });
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
await page.goto(pathToFileURL(join(HERE, 'scenes.html')).href);
await page.evaluate(async tl => { await window.ready; window.setup(tl); }, TL);

const picture = join(BUILD, 'picture.mp4');
const frames = Math.ceil(TL.total * TL.fps);
const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(TL.fps), '-c:v', 'mjpeg',
  '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', QUICK ? '28' : '17', '-preset', QUICK ? 'veryfast' : 'slow',
  '-vf', 'scale=1920:1080:flags=lanczos', picture], { stdio: ['pipe', 'inherit', 'inherit'] });
const done = new Promise(r => ff.on('close', r));

const t0 = Date.now();
for (let f = 0; f < frames; f++) {
  await page.evaluate(t => window.render(t), f / TL.fps);
  const jpg = await page.screenshot({ type: 'jpeg', quality: QUICK ? 80 : 95 });
  if (!ff.stdin.write(jpg)) await new Promise(r => ff.stdin.once('drain', r));
  if (f % TL.fps === 0) process.stdout.write(`\rframe ${f}/${frames}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
}
ff.stdin.end(); await done; await browser.close();
console.log(`\rpicture: ${frames} frames in ${((Date.now() - t0) / 1000).toFixed(0)}s`);

// ── sound: ambient pad + wake chime + narration ────────────────────────────
const T = TL.total, chimeAt = TL.scenes[1].start + 0.35;
const pad = `aevalsrc='0.05*(sin(2*PI*110*t)+0.7*sin(2*PI*164.81*t)+0.5*sin(2*PI*220*t)+0.35*sin(2*PI*329.63*t))*(0.75+0.25*sin(2*PI*0.15*t))':s=48000:d=${T}`;
const chime = `aevalsrc='0.22*exp(-5*t)*sin(2*PI*880*t)+0.16*exp(-4*(t-0.12))*gte(t,0.12)*sin(2*PI*1318.5*t)':s=48000:d=1.6`;
const inputs = ['-i', picture, '-f', 'lavfi', '-i', pad, '-f', 'lavfi', '-i', chime];
const graph = [
  `[1]lowpass=f=1400,afade=t=in:d=1.5,afade=t=out:st=${T - 2}:d=2,volume=0.9[pad]`,
  `[2]adelay=${Math.round(chimeAt * 1000)}:all=1[chime]`,
];
const mix = ['[pad]', '[chime]'];
TL.scenes.forEach((s, i) => {
  const wav = s.wav && join(BUILD, s.wav);
  if (!wav || !existsSync(wav)) return;
  inputs.push('-i', wav);
  const idx = inputs.filter(x => x === '-i').length - 1;
  graph.push(`[${idx}]aresample=48000,adelay=${Math.round(s.voiceStart * 1000)}:all=1,volume=1.6[v${i}]`);
  mix.push(`[v${i}]`);
});
if (mix.length === 2) console.log('no narration WAVs found — music only');
graph.push(`${mix.join('')}amix=inputs=${mix.length}:normalize=0:duration=first,alimiter=limit=0.95[a]`);

run('ffmpeg', ['-v', 'error', '-y', ...inputs, '-filter_complex', graph.join(';'),
  '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', String(T),
  '-movflags', '+faststart', OUT]);
console.log(`✓ ${OUT}`);
