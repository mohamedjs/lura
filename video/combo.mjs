// Lura combo: rebuilds lura_combo_9x16.mp4 with the new face, adds a human calling
// «لورا» before Lura answers, and renders it 9:16 and/or 16:9, dark and/or light.
//   node combo.mjs --source lura_combo_9x16.mp4 [--human human_lura.wav] [--aspect v|h|both] [--theme dark|light|both] [--preview]
import { existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { envelope, renderPage, run } from './frames.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const B = join(HERE, 'build', 'combo');
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const SOURCE = arg('--source'), HUMAN = arg('--human', join(B, 'human_lura.wav'));
const ASPECTS = { v: ['v'], h: ['h'], both: ['v', 'h'] }[arg('--aspect', 'both')];
const THEMES = { dark: ['dark'], light: ['light'], both: ['dark', 'light'] }[arg('--theme', 'both')];
const QUICK = process.argv.includes('--preview');
const OUTDIR = resolve(arg('--outdir', join(HERE, 'out')));
if (!SOURCE || !existsSync(SOURCE)) { console.error('need --source lura_combo_9x16.mp4'); process.exit(1); }
mkdirSync(B, { recursive: true }); mkdirSync(OUTDIR, { recursive: true });
const TL = { fps: 30, total: 78.4, clips: {} };

// narration from the source; the mouth follows it
const narration = join(B, 'narration.wav');
run('ffmpeg', ['-v', 'error', '-y', '-i', resolve(SOURCE), '-vn', '-ac', '2', '-ar', '48000', narration]);
TL.env = envelope(narration, TL.fps);

// + the human wake call at 8.1 s, source ducked under it
const voice = join(B, 'voice.wav');
if (existsSync(HUMAN)) {
  run('ffmpeg', ['-v', 'error', '-y', '-i', narration, '-i', HUMAN, '-filter_complex',
    "[0]volume='if(between(t,7.95,8.75),0.45,1)':eval=frame[s];[1]aresample=48000,adelay=8100:all=1,volume=1.15[h];" +
    '[s][h]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95', voice]);
} else { console.log(`no ${HUMAN} — narration only`); run('ffmpeg', ['-v', 'error', '-y', '-i', narration, voice]); }

// real screen recordings, cropped out of the source's cards (inside the border, under its label)
const CLIPS = { A: [7.0, 6.0, 712], B: [13.8, 11.6, 232], C: [63.0, 6.6, 632] };   // start, length, crop top
for (const [name, [start, len, y]] of Object.entries(CLIPS)) {
  const dir = join(B, `clip${name}`);
  rmSync(dir, { recursive: true, force: true }); mkdirSync(dir);
  run('ffmpeg', ['-v', 'error', '-ss', String(start), '-i', resolve(SOURCE), '-t', String(len),
    '-vf', `crop=948:458:66:${y},fps=${TL.fps},scale=980:474`, '-q:v', '3', join(dir, 'f%04d.jpg')]);
  TL.clips[name] = { dir: `build/combo/clip${name}`, start, count: readdirSync(dir).length };
}

for (const aspect of ASPECTS) for (const theme of THEMES) {
  const [w, h] = aspect === 'v' ? [1080, 1920] : [1920, 1080];
  const tag = `${aspect === 'v' ? '9x16' : '16x9'}_${theme}`;
  const picture = join(B, `picture_${tag}.mp4`), out = join(OUTDIR, `lura_combo_${tag}.mp4`);
  console.log(`— ${tag}`);
  await renderPage({ html: join(HERE, 'combo.html'), TL: { ...TL, aspect, theme }, width: w, height: h, out: picture, quick: QUICK });
  run('ffmpeg', ['-v', 'error', '-y', '-i', picture, '-i', voice, '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
    '-c:a', 'aac', '-b:a', '192k', '-t', String(TL.total), '-movflags', '+faststart', out]);
  console.log(`✓ ${out}`);
}
