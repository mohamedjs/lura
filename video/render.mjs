// Frame-grabs scenes.html with headless Chromium, then muxes narration + music.
//   node render.mjs [--footage ~/Downloads/clip.mp4 --footage-start 3] [--out out/lura.mp4]
import { existsSync, mkdirSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { envelope, renderPage, run } from './frames.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, 'build');
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const OUT = resolve(arg('--out', join(HERE, 'out', 'lura.mp4')));
const FOOTAGE = arg('--footage');
const FOOT_START = Number(arg('--footage-start', '0'));
const QUICK = process.argv.includes('--preview');   // half-res, fast check

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
  if (wav && existsSync(wav)) s.env = envelope(wav, TL.fps);
}

// ── picture ────────────────────────────────────────────────────────────────
const picture = join(BUILD, 'picture.mp4');
await renderPage({ html: join(HERE, 'scenes.html'), TL, width: 1920, height: 1080, out: picture, quick: QUICK });

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
